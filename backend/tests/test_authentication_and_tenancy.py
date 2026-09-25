import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.clerk import ClerkAuthProvider
from app.auth.dependencies import get_auth_provider
from app.auth.errors import AuthenticationError
from app.auth.provisioning import get_or_create_user
from app.auth.test_provider import TestAuthProvider
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.main import app
from app.models.dataset import Dataset, DatasetCase
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.models.user import User
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_auth_client(
    default_user_id: str | None = None,
) -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient with TestAuthProvider for multi-tenant testing."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_provider = TestAuthProvider(default_user_id=default_user_id)
    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: test_provider
    app.dependency_overrides[get_run_executor] = lambda: SynchronousRunExecutor()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


# =========================================================================
# 1. Unauthenticated & Malformed Request Tests
# =========================================================================


@pytest.mark.asyncio
async def test_missing_auth_header_returns_401_with_www_authenticate() -> None:
    # default_user_id=None forces explicit auth header requirement
    async with managed_auth_client(default_user_id=None) as client:
        # Test dataset routes
        res1 = await client.get("/api/v1/datasets")
        assert res1.status_code == 401
        assert "Bearer" in res1.headers.get("WWW-Authenticate", "")

        res2 = await client.post("/api/v1/datasets", json={"name": "Unauth DS"})
        assert res2.status_code == 401
        assert "Bearer" in res2.headers.get("WWW-Authenticate", "")

        # Test evaluation run routes
        res3 = await client.get("/api/v1/evaluations/runs")
        assert res3.status_code == 401
        assert "Bearer" in res3.headers.get("WWW-Authenticate", "")

        res4 = await client.post(
            "/api/v1/evaluations/runs",
            json={"dataset_id": str(uuid.uuid4()), "evaluators": []},
        )
        assert res4.status_code == 401
        assert "Bearer" in res4.headers.get("WWW-Authenticate", "")


@pytest.mark.asyncio
async def test_malformed_auth_header_returns_401() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        # Not Bearer scheme
        res = await client.get(
            "/api/v1/datasets",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert res.status_code == 401
        assert "Malformed" in res.json()["detail"]


@pytest.mark.asyncio
async def test_expired_token_returns_401() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        res = await client.get(
            "/api/v1/datasets",
            headers={"Authorization": "Bearer expired"},
        )
        assert res.status_code == 401
        assert "expired" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_tampered_or_invalid_token_returns_401() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        res1 = await client.get(
            "/api/v1/datasets",
            headers={"Authorization": "Bearer invalid"},
        )
        assert res1.status_code == 401

        res2 = await client.get(
            "/api/v1/datasets",
            headers={"Authorization": "Bearer tampered"},
        )
        assert res2.status_code == 401


# =========================================================================
# 2. Public Health Endpoints Remain Accessible Without Auth
# =========================================================================


@pytest.mark.asyncio
async def test_public_health_and_root_endpoints_require_no_auth() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        # Root endpoint
        root_res = await client.get("/")
        assert root_res.status_code == 200
        assert root_res.json()["name"] == "EVALX"

        # Health endpoints
        h1 = await client.get("/api/v1/health")
        assert h1.status_code == 200
        assert h1.json()["status"] == "ok"

        h2 = await client.get("/api/v1/health/live")
        assert h2.status_code == 200

        h3 = await client.get("/api/v1/health/ready")
        assert h3.status_code == 200


# =========================================================================
# 3. User Provisioning & Concurrency Safety Tests
# =========================================================================


@pytest.mark.asyncio
async def test_user_provisioning_on_first_request_and_reuse() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id=None) as client:
        # First request as alice
        alice_headers = {"Authorization": "Bearer clerk_alice_123"}
        res1 = await client.get("/api/v1/datasets", headers=alice_headers)
        assert res1.status_code == 200

        # Verify alice was provisioned in database
        async with session_factory() as session:
            stmt = select(User).where(User.external_auth_id == "clerk_alice_123")
            alice_user = (await session.execute(stmt)).scalar_one_or_none()
            assert alice_user is not None
            assert alice_user.external_auth_id == "clerk_alice_123"
            alice_id = alice_user.id

        # Second request as alice: verify reuse of existing User
        res2 = await client.get("/api/v1/datasets", headers=alice_headers)
        assert res2.status_code == 200

        async with session_factory() as session:
            all_users = (await session.scalars(select(User))).all()
            assert len(all_users) == 1
            assert all_users[0].id == alice_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_user_provisioning_race_condition() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    external_id = f"concurrent_user_{uuid.uuid4().hex[:8]}"

    async def provision_worker() -> User:
        async with session_factory() as session:
            return await get_or_create_user(session, external_auth_id=external_id)

    # Launch 5 concurrent provisioning calls simultaneously
    users = await asyncio.gather(
        provision_worker(),
        provision_worker(),
        provision_worker(),
        provision_worker(),
        provision_worker(),
    )

    # All workers must resolve to the exact same User ID
    first_id = users[0].id
    assert all(u.id == first_id for u in users)

    async with session_factory() as session:
        stmt = select(User).where(User.external_auth_id == external_id)
        matching = (await session.scalars(stmt)).all()
        assert len(matching) == 1

    await engine.dispose()


# =========================================================================
# 4. Multi-Tenant Resource Isolation Tests (User A vs User B)
# =========================================================================


@pytest.mark.asyncio
async def test_dataset_isolation_and_anti_idor() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        alice = {"Authorization": "Bearer tenant_alice"}
        bob = {"Authorization": "Bearer tenant_bob"}

        # Alice creates Dataset A
        alice_create_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Alice Private Dataset", "description": "Top secret"},
            headers=alice,
        )
        assert alice_create_res.status_code == 201
        dataset_a_id = alice_create_res.json()["id"]

        # Bob lists datasets: should be empty (count=0, items=[])
        bob_list_res = await client.get("/api/v1/datasets", headers=bob)
        assert bob_list_res.status_code == 200
        bob_data = bob_list_res.json()
        assert bob_data["total"] == 0
        assert len(bob_data["items"]) == 0

        # Bob searches for Alice's dataset name: should return empty
        bob_search_res = await client.get("/api/v1/datasets?search=Alice", headers=bob)
        assert bob_search_res.status_code == 200
        assert bob_search_res.json()["total"] == 0

        # Bob attempts direct access to Alice's dataset: MUST RETURN 404 (NEVER 403)
        bob_get_res = await client.get(f"/api/v1/datasets/{dataset_a_id}", headers=bob)
        assert bob_get_res.status_code == 404
        assert bob_get_res.json() == {"detail": "Dataset not found"}

        # Bob attempts PATCH on Alice's dataset: 404
        bob_patch_res = await client.patch(
            f"/api/v1/datasets/{dataset_a_id}",
            json={"name": "Hacked Name"},
            headers=bob,
        )
        assert bob_patch_res.status_code == 404

        # Bob attempts DELETE on Alice's dataset: 404
        bob_del_res = await client.delete(
            f"/api/v1/datasets/{dataset_a_id}", headers=bob
        )
        assert bob_del_res.status_code == 404

        # Verify Alice's dataset remains intact
        alice_get_res = await client.get(
            f"/api/v1/datasets/{dataset_a_id}", headers=alice
        )
        assert alice_get_res.status_code == 200
        assert alice_get_res.json()["name"] == "Alice Private Dataset"


@pytest.mark.asyncio
async def test_dataset_case_isolation() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        alice = {"Authorization": "Bearer tenant_alice"}
        bob = {"Authorization": "Bearer tenant_bob"}

        # Alice creates Dataset A and Case A
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Alice Cases DS"},
            headers=alice,
        )
        assert ds_res.status_code == 201
        dataset_id = ds_res.json()["id"]

        case_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "Alice Input", "expected_output": "Alice Output"},
            headers=alice,
        )
        assert case_res.status_code == 201
        case_id = case_res.json()["id"]

        # Bob attempts to view cases of Alice's dataset: 404
        bob_list_cases = await client.get(
            f"/api/v1/datasets/{dataset_id}/cases", headers=bob
        )
        assert bob_list_cases.status_code == 404

        # Bob attempts to view specific case: 404
        bob_get_case = await client.get(
            f"/api/v1/datasets/{dataset_id}/cases/{case_id}", headers=bob
        )
        assert bob_get_case.status_code == 404

        # Bob attempts to insert case into Alice's dataset: 404
        bob_add_case = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "Bob Injected Case", "expected_output": "Bad"},
            headers=bob,
        )
        assert bob_add_case.status_code == 404

        # Bob attempts bulk insert: 404
        bob_bulk = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk",
            json={"cases": [{"input": "Bulk Bob", "expected_output": "Bob"}]},
            headers=bob,
        )
        assert bob_bulk.status_code == 404

        # Bob attempts validation: 404
        bob_val = await client.post(
            f"/api/v1/datasets/{dataset_id}/validate", headers=bob
        )
        assert bob_val.status_code == 404

        # Bob attempts PATCH on case: 404
        bob_patch = await client.patch(
            f"/api/v1/datasets/{dataset_id}/cases/{case_id}",
            json={"input": "Tampered"},
            headers=bob,
        )
        assert bob_patch.status_code == 404

        # Bob attempts DELETE on case: 404
        bob_del = await client.delete(
            f"/api/v1/datasets/{dataset_id}/cases/{case_id}", headers=bob
        )
        assert bob_del.status_code == 404


@pytest.mark.asyncio
async def test_evaluation_run_isolation() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        alice = {"Authorization": "Bearer tenant_alice"}
        bob = {"Authorization": "Bearer tenant_bob"}

        # Alice creates Dataset A with 1 case
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Alice Eval DS"},
            headers=alice,
        )
        assert ds_res.status_code == 201
        dataset_id = ds_res.json()["id"]

        await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "What is 2+2?", "expected_output": "4"},
            headers=alice,
        )

        # Bob attempts to launch an evaluation run against Alice's dataset: 404
        bob_launch = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": dataset_id,
                "name": "Bob's Run on Alice's DS",
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
                "responses": [{"case_id": str(uuid.uuid4()), "response": "4"}],
            },
            headers=bob,
        )
        assert bob_launch.status_code == 404
        assert "Dataset" in bob_launch.json()["detail"]

        # Alice launches an evaluation run
        cases_list = (
            await client.get(f"/api/v1/datasets/{dataset_id}/cases", headers=alice)
        ).json()["items"]
        case_id = cases_list[0]["id"]

        alice_launch = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": dataset_id,
                "name": "Alice's Authorized Run",
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
                "responses": [{"case_id": case_id, "response": "4"}],
            },
            headers=alice,
        )
        assert alice_launch.status_code in (201, 202)
        run_id = alice_launch.json()["id"]

        # Bob lists evaluation runs: should not see Alice's run
        bob_runs = await client.get("/api/v1/evaluations/runs", headers=bob)
        assert bob_runs.status_code == 200
        assert bob_runs.json()["total"] == 0
        assert len(bob_runs.json()["items"]) == 0

        # Bob gets Alice's run directly: 404
        bob_run = await client.get(f"/api/v1/evaluations/runs/{run_id}", headers=bob)
        assert bob_run.status_code == 404

        # Bob gets Alice's run results: 404
        bob_results = await client.get(
            f"/api/v1/evaluations/runs/{run_id}/results", headers=bob
        )
        assert bob_results.status_code == 404

        # Alice can access her run and results
        alice_run = await client.get(
            f"/api/v1/evaluations/runs/{run_id}", headers=alice
        )
        assert alice_run.status_code == 200
        assert alice_run.json()["id"] == run_id

        alice_results = await client.get(
            f"/api/v1/evaluations/runs/{run_id}/results", headers=alice
        )
        assert alice_results.status_code == 200
        assert alice_results.json()["total"] == 1


# =========================================================================
# 5. Legacy NULL-Owner Isolation Tests
# =========================================================================


@pytest.mark.asyncio
async def test_legacy_null_owner_datasets_are_completely_inaccessible() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Insert an unowned legacy dataset directly
    async with session_factory() as session:
        legacy_ds = Dataset(
            name="Legacy Unowned Dev Dataset",
            description="Created prior to Step 07",
            version=1,
            owner_user_id=None,  # Explicitly NULL
        )
        session.add(legacy_ds)
        await session.commit()
        await session.refresh(legacy_ds)
        legacy_id = legacy_ds.id

    async with managed_auth_client(default_user_id=None) as client:
        alice = {"Authorization": "Bearer tenant_alice"}

        # 1. Listing must NOT include legacy NULL-owner datasets
        list_res = await client.get("/api/v1/datasets", headers=alice)
        assert list_res.status_code == 200
        assert list_res.json()["total"] == 0
        assert all(d["id"] != str(legacy_id) for d in list_res.json()["items"])

        # 2. Search must NOT match legacy NULL-owner datasets
        search_res = await client.get("/api/v1/datasets?search=Legacy", headers=alice)
        assert search_res.status_code == 200
        assert search_res.json()["total"] == 0

        # 3. Direct GET must return 404
        get_res = await client.get(f"/api/v1/datasets/{legacy_id}", headers=alice)
        assert get_res.status_code == 404

        # 4. Evaluation run against legacy dataset must return 404
        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": str(legacy_id),
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
            },
            headers=alice,
        )
        assert run_res.status_code == 404

    await engine.dispose()


# =========================================================================
# 6. User Deletion & Evaluation Data Retention Tests
# =========================================================================


@pytest.mark.asyncio
async def test_user_deletion_retains_dataset_and_evaluation_history() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id=None) as client:
        alice = {"Authorization": "Bearer user_alice_to_delete"}
        charlie = {"Authorization": "Bearer user_charlie"}

        # 1. Alice creates dataset
        ds_res = await client.post(
            "/api/v1/datasets",
            json={
                "name": "Alice Retention Dataset",
                "description": "Must survive user deletion",
            },
            headers=alice,
        )
        assert ds_res.status_code == 201
        dataset_id = uuid.UUID(ds_res.json()["id"])

        # Add case
        case_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "Question 1", "expected_output": "Answer 1"},
            headers=alice,
        )
        assert case_res.status_code == 201
        case_id = uuid.UUID(case_res.json()["id"])

        # Launch run
        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": str(dataset_id),
                "name": "Retention Run",
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
                "responses": [{"case_id": str(case_id), "response": "Answer 1"}],
            },
            headers=alice,
        )
        assert run_res.status_code in (201, 202)
        run_id = uuid.UUID(run_res.json()["id"])

        # Confirm Alice's user exists in DB
        async with session_factory() as session:
            stmt = select(User).where(User.external_auth_id == "user_alice_to_delete")
            alice_user = (await session.execute(stmt)).scalar_one()
            alice_user_id = alice_user.id

            # 2. Delete Alice's user directly from DB
            await session.delete(alice_user)
            await session.commit()

        # 3. Verify in DB that data is retained with owner_user_id = NULL
        async with session_factory() as session:
            # - User is deleted
            deleted_user = await session.get(User, alice_user_id)
            assert deleted_user is None

            # - Dataset is RETAINED and owner_user_id is NULL
            dataset = await session.get(Dataset, dataset_id)
            assert dataset is not None
            assert dataset.owner_user_id is None
            assert dataset.name == "Alice Retention Dataset"

            # - DatasetCase is retained
            case = await session.get(DatasetCase, case_id)
            assert case is not None

            # - EvaluationRun is retained
            run = await session.get(EvaluationRun, run_id)
            assert run is not None

            # - EvaluationResult rows are retained
            res_stmt = select(EvaluationResult).where(EvaluationResult.run_id == run_id)
            results = (await session.scalars(res_stmt)).all()
            assert len(results) == 1

        # 4. Authenticated users (e.g. Charlie) cannot access the now-unowned dataset:
        charlie_get = await client.get(
            f"/api/v1/datasets/{dataset_id}", headers=charlie
        )
        assert charlie_get.status_code == 404

        # 5. Counts, search, and listings exclude the now-unowned dataset:
        charlie_list = await client.get("/api/v1/datasets", headers=charlie)
        assert charlie_list.status_code == 200
        assert charlie_list.json()["total"] == 0
        assert all(d["id"] != str(dataset_id) for d in charlie_list.json()["items"])

        charlie_search = await client.get(
            "/api/v1/datasets?search=Retention", headers=charlie
        )
        assert charlie_search.status_code == 200
        assert charlie_search.json()["total"] == 0

        # 6. Cannot launch run against now-unowned dataset:
        charlie_launch = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": str(dataset_id),
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
            },
            headers=charlie,
        )
        assert charlie_launch.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_evaluation_id_isolation() -> None:
    async with managed_auth_client(default_user_id=None) as client:
        alice = {"Authorization": "Bearer user_alice_eval"}
        bob = {"Authorization": "Bearer user_bob_eval"}

        # Alice creates dataset, case, and run (which creates Evaluation record)
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Alice Eval Isolation DS"},
            headers=alice,
        )
        assert ds_res.status_code == 201
        alice_ds_id = ds_res.json()["id"]

        case_res = await client.post(
            f"/api/v1/datasets/{alice_ds_id}/cases",
            json={"input": "Q", "expected_output": "A"},
            headers=alice,
        )
        alice_case_id = case_res.json()["id"]

        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": alice_ds_id,
                "name": "Alice Initial Run",
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
                "responses": [{"case_id": alice_case_id, "response": "A"}],
            },
            headers=alice,
        )
        assert run_res.status_code in (201, 202)
        alice_eval_id = run_res.json()["evaluation_id"]

        # Bob creates his own dataset and case
        bob_ds = await client.post(
            "/api/v1/datasets",
            json={"name": "Bob's Own DS"},
            headers=bob,
        )
        bob_ds_id = bob_ds.json()["id"]

        await client.post(
            f"/api/v1/datasets/{bob_ds_id}/cases",
            json={"input": "Bob Question", "expected_output": "Bob Answer"},
            headers=bob,
        )

        # Bob attempts to reuse Alice's evaluation_id: MUST RETURN 404 (IDOR prevention)
        bob_run = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": bob_ds_id,
                "evaluation_id": alice_eval_id,
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
            },
            headers=bob,
        )
        assert bob_run.status_code == 404
        assert "Evaluation" in bob_run.json()["detail"]


# =========================================================================
# 7. CORS Configuration Verification
# =========================================================================


@pytest.mark.asyncio
async def test_cors_headers_on_allowed_origins() -> None:
    async with managed_auth_client(default_user_id="test-user") as client:
        # Preflight OPTIONS request from allowed origin
        res = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert res.headers.get("access-control-allow-credentials") == "true"


# =========================================================================
# 7. ClerkAuthProvider Networkless JWT Verification Unit Tests
# =========================================================================


def test_clerk_pem_formatting() -> None:
    from app.auth.clerk import _format_pem_key

    # Already formatted PEM
    already_pem = (
        "-----BEGIN PUBLIC KEY-----\n"
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A\n"
        "-----END PUBLIC KEY-----"
    )
    assert _format_pem_key(already_pem) == already_pem

    # Raw base64 string
    raw_key = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA"
    formatted = _format_pem_key(raw_key)
    assert formatted.startswith("-----BEGIN PUBLIC KEY-----\n")
    assert formatted.endswith("\n-----END PUBLIC KEY-----\n")


def test_clerk_jwt_networkless_verification() -> None:
    import time

    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate ephemeral RSA key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    pem_public = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    provider = ClerkAuthProvider(
        jwt_key=pem_public,
        authorized_parties=["https://evalx.local"],
    )

    # 1. Valid token
    now = int(time.time())
    payload = {
        "sub": "user_clerk_rsa_123",
        "sid": "sess_123",
        "azp": "https://evalx.local",
        "email": "clerk_user@example.com",
        "iat": now,
        "exp": now + 3600,
    }
    valid_token = jwt.encode(payload, pem_private, algorithm="RS256")
    principal = provider._verify_jwt_token(valid_token)
    assert principal.is_authenticated is True
    assert principal.external_user_id == "user_clerk_rsa_123"
    assert principal.email == "clerk_user@example.com"
    assert principal.session_id == "sess_123"

    # 2. Expired token
    expired_payload = dict(payload, exp=now - 100)
    expired_token = jwt.encode(expired_payload, pem_private, algorithm="RS256")
    with pytest.raises(AuthenticationError, match="expired"):
        provider._verify_jwt_token(expired_token)

    # 3. Unauthorized party (azp mismatch)
    azp_mismatch_payload = dict(payload, azp="https://malicious.party")
    mismatch_token = jwt.encode(azp_mismatch_payload, pem_private, algorithm="RS256")
    with pytest.raises(AuthenticationError, match="Unauthorized party"):
        provider._verify_jwt_token(mismatch_token)

    # 4. Missing sub claim
    no_sub_payload = {k: v for k, v in payload.items() if k != "sub"}
    no_sub_token = jwt.encode(no_sub_payload, pem_private, algorithm="RS256")
    with pytest.raises(AuthenticationError, match="sub"):
        provider._verify_jwt_token(no_sub_token)

    # 5. Tampered token signature
    tampered_token = valid_token[:-4] + "wxyz"
    with pytest.raises(AuthenticationError, match="Invalid or tampered"):
        provider._verify_jwt_token(tampered_token)
