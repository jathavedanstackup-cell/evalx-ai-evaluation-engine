import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_auth_provider
from app.auth.test_provider import TestAuthProvider
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.resolver import ConfigurationResolver
from app.evaluation.scoring import calculate_case_score
from app.evaluation.snapshot import compute_configuration_snapshot
from app.evaluation.types import EvaluationMetric
from app.main import app
from app.models.evaluation_configuration import (
    EvaluationConfig,
    EvaluationConfigVersion,
)
from app.models.user import User
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_config_client(
    default_user_id: str | None = None,
) -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient connected to TEST database with TestAuthProvider."""
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
                    "evaluation_results, evaluation_configs, "
                    "evaluation_config_versions CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


# =========================================================================
# 1. Snapshot Computation, Serialization & Secret Scrubbing Tests
# =========================================================================


def test_snapshot_computation_deterministic() -> None:
    evaluators_1 = [
        {
            "name": "metric_b",
            "evaluator_type": "relevance",
            "backend": "llm_judge",
            "weight": 2.0,
            "threshold": 0.7,
            "configuration": {"b": 2, "a": 1},
        },
        {
            "name": "metric_a",
            "evaluator_type": "factuality",
            "backend": "llm_judge",
            "weight": 1.0,
            "threshold": 0.8,
            "configuration": {"rules": ["rule1"]},
        },
    ]

    evaluators_2 = [
        {
            "name": "metric_a",
            "evaluator_type": "factuality",
            "backend": "llm_judge",
            "weight": 1.0,
            "threshold": 0.8,
            "configuration": {"rules": ["rule1"]},
        },
        {
            "name": "metric_b",
            "evaluator_type": "relevance",
            "backend": "llm_judge",
            "weight": 2.0,
            "threshold": 0.7,
            "configuration": {"a": 1, "b": 2},  # Reordered dict keys
        },
    ]

    snap1, hash1 = compute_configuration_snapshot(
        name="Preset 1",
        description="Test preset",
        version=1,
        evaluators=evaluators_1,
    )

    snap2, hash2 = compute_configuration_snapshot(
        name="Preset 1",
        description="Test preset",
        version=1,
        evaluators=evaluators_2,
    )

    # Hashes and serialized canonical snapshots must be identical despite key ordering
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex digest
    assert snap1["evaluators"] == snap2["evaluators"]


def test_snapshot_scrubs_sensitive_secrets() -> None:
    evaluators = [
        {
            "evaluator_type": "factuality",
            "backend": "llm_judge",
            "configuration": {
                "api_key": "sk-secret-12345",
                "auth_token": "bearer-token-abc",
                "client_secret": "my-client-secret",
                "temperature": 0.2,
            },
        }
    ]

    snap, h = compute_configuration_snapshot(
        name="Secure Config",
        description=None,
        version=1,
        evaluators=evaluators,
    )

    cfg = snap["evaluators"][0]["configuration"]
    assert cfg["api_key"] == "[REDACTED]"
    assert cfg["auth_token"] == "[REDACTED]"
    assert cfg["client_secret"] == "[REDACTED]"
    assert cfg["temperature"] == 0.2
    assert "sk-secret" not in str(snap)


def test_snapshot_hash_changes_on_any_field_mutation() -> None:
    base_evals = [
        {"evaluator_type": "factuality", "backend": "llm_judge", "weight": 1.0}
    ]
    _, base_hash = compute_configuration_snapshot("Config", None, 1, base_evals)

    # Name change
    _, name_hash = compute_configuration_snapshot("Config2", None, 1, base_evals)
    assert name_hash != base_hash

    # Version change
    _, ver_hash = compute_configuration_snapshot("Config", None, 2, base_evals)
    assert ver_hash != base_hash

    # Weight change
    mutated_evals = [
        {"evaluator_type": "factuality", "backend": "llm_judge", "weight": 2.0}
    ]
    _, weight_hash = compute_configuration_snapshot("Config", None, 1, mutated_evals)
    assert weight_hash != base_hash


# =========================================================================
# 2. Configuration Resolver Unit Tests
# =========================================================================


@pytest.mark.asyncio
async def test_configuration_resolver() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user = User(external_auth_id="clerk_resolver_1", email="resolver@test.com")
        session.add(user)
        await session.flush()

        evals_v1 = [
            {
                "evaluator_type": "factuality",
                "backend": "llm_judge",
                "weight": 1.0,
                "enabled": True,
            }
        ]
        snap_v1, hash_v1 = compute_configuration_snapshot(
            "ResConfig", None, 1, evals_v1
        )

        config = EvaluationConfig(
            name="ResConfig",
            description=None,
            version=1,
            evaluators=evals_v1,
            snapshot_hash=hash_v1,
            owner_user_id=user.id,
        )
        session.add(config)
        await session.flush()

        # Save historical version 1
        v1_rec = EvaluationConfigVersion(
            config_id=config.id,
            version=1,
            name=config.name,
            description=config.description,
            evaluators=evals_v1,
            snapshot_hash=hash_v1,
        )
        session.add(v1_rec)

        # Mutate config to version 2
        evals_v2 = [
            {
                "evaluator_type": "factuality",
                "backend": "llm_judge",
                "weight": 3.0,
                "enabled": True,
            }
        ]
        _, hash_v2 = compute_configuration_snapshot("ResConfig", None, 2, evals_v2)
        config.version = 2
        config.evaluators = evals_v2
        config.snapshot_hash = hash_v2
        await session.commit()

        # 1. Resolve active (latest) version
        resolved_latest = await ConfigurationResolver.resolve_configuration(
            session=session,
            config_id=config.id,
            owner_user_id=user.id,
            version=None,
        )
        assert resolved_latest is not None
        assert resolved_latest.version == 2
        assert resolved_latest.snapshot_hash == hash_v2
        assert resolved_latest.evaluators[0]["weight"] == 3.0

        # 2. Resolve specific historical version 1
        resolved_v1 = await ConfigurationResolver.resolve_configuration(
            session=session,
            config_id=config.id,
            owner_user_id=user.id,
            version=1,
        )
        assert resolved_v1 is not None
        assert resolved_v1.version == 1
        assert resolved_v1.snapshot_hash == hash_v1
        assert resolved_v1.evaluators[0]["weight"] == 1.0

        # 3. Resolve non-existent version
        resolved_v99 = await ConfigurationResolver.resolve_configuration(
            session=session,
            config_id=config.id,
            owner_user_id=user.id,
            version=99,
        )
        assert resolved_v99 is None

        # 4. Cross-tenant resolution raises 404 (None returned)
        other_user_id = uuid.uuid4()
        resolved_cross = await ConfigurationResolver.resolve_configuration(
            session=session,
            config_id=config.id,
            owner_user_id=other_user_id,
            version=None,
        )
        assert resolved_cross is None

        # Clean up
        await session.execute(
            text(
                "TRUNCATE TABLE users, evaluation_configs, "
                "evaluation_config_versions CASCADE"
            )
        )
        await session.commit()
    await engine.dispose()


# =========================================================================
# 3. Configuration API CRUD & Versioning Integration Tests
# =========================================================================


@pytest.mark.asyncio
async def test_configuration_crud_and_versioning_flow() -> None:
    alice_headers = {"Authorization": "Bearer test-user-alice"}

    async with managed_config_client() as client:
        # 1. Create a configuration (v1)
        create_payload = {
            "name": "Standard Production Preset",
            "description": "Factuality + Relevance multi-metric config",
            "evaluators": [
                {
                    "evaluator_type": "factuality",
                    "backend": "llm_judge",
                    "name": "prod_factuality",
                    "threshold": 0.85,
                    "weight": 2.0,
                    "configuration": {"model": "gemini-1.5-pro"},
                },
                {
                    "evaluator_type": "relevance",
                    "backend": "llm_judge",
                    "name": "prod_relevance",
                    "threshold": 0.70,
                    "weight": 1.0,
                    "configuration": {},
                },
            ],
        }
        res = await client.post(
            "/api/v1/evaluation-configs",
            json=create_payload,
            headers=alice_headers,
        )
        assert res.status_code == 201
        cfg_data = res.json()
        config_id = cfg_data["id"]
        assert cfg_data["name"] == "Standard Production Preset"
        assert cfg_data["version"] == 1
        assert len(cfg_data["snapshot_hash"]) == 64
        assert len(cfg_data["evaluators"]) == 2
        v1_hash = cfg_data["snapshot_hash"]

        # 2. Get active configuration by ID
        res = await client.get(
            f"/api/v1/evaluation-configs/{config_id}",
            headers=alice_headers,
        )
        assert res.status_code == 200
        get_data = res.json()
        assert get_data["id"] == config_id
        assert get_data["version"] == 1

        # 3. List configurations
        res = await client.get(
            "/api/v1/evaluation-configs?page=1&page_size=10",
            headers=alice_headers,
        )
        assert res.status_code == 200
        list_data = res.json()
        assert list_data["total"] == 1
        assert list_data["page"] == 1
        assert list_data["items"][0]["id"] == config_id

        # 4. Update configuration (creates version 2)
        update_payload = {
            "name": "Standard Production Preset v2",
            "evaluators": [
                {
                    "evaluator_type": "factuality",
                    "backend": "llm_judge",
                    "name": "prod_factuality",
                    "threshold": 0.90,
                    "weight": 3.0,
                    "configuration": {"model": "gemini-1.5-pro"},
                },
                {
                    "evaluator_type": "consistency",
                    "backend": "llm_judge",
                    "name": "prod_consistency",
                    "threshold": 0.80,
                    "weight": 1.5,
                    "configuration": {},
                },
            ],
        }
        res = await client.patch(
            f"/api/v1/evaluation-configs/{config_id}",
            json=update_payload,
            headers=alice_headers,
        )
        assert res.status_code == 200
        updated_data = res.json()
        assert updated_data["version"] == 2
        assert updated_data["name"] == "Standard Production Preset v2"
        v2_hash = updated_data["snapshot_hash"]
        assert v2_hash != v1_hash
        assert len(updated_data["evaluators"]) == 2

        # 5. Fetch version 1 historically
        res_v1 = await client.get(
            f"/api/v1/evaluation-configs/{config_id}?version=1",
            headers=alice_headers,
        )
        assert res_v1.status_code == 200
        v1_data = res_v1.json()
        assert v1_data["version"] == 1
        assert v1_data["snapshot_hash"] == v1_hash
        assert v1_data["evaluators"][0]["weight"] == 2.0
        assert v1_data["evaluators"][1]["name"] == "prod_relevance"

        # 6. Fetch active (version 2)
        res_v2 = await client.get(
            f"/api/v1/evaluation-configs/{config_id}",
            headers=alice_headers,
        )
        assert res_v2.status_code == 200
        assert res_v2.json()["version"] == 2
        assert res_v2.json()["evaluators"][1]["name"] == "prod_consistency"

        # 7. Delete configuration
        del_res = await client.delete(
            f"/api/v1/evaluation-configs/{config_id}",
            headers=alice_headers,
        )
        assert del_res.status_code == 204

        # 8. Verify 404 after deletion
        res_after = await client.get(
            f"/api/v1/evaluation-configs/{config_id}",
            headers=alice_headers,
        )
        assert res_after.status_code == 404


# =========================================================================
# 4. Multi-Tenant Isolation Tests
# =========================================================================


@pytest.mark.asyncio
async def test_configuration_tenancy_isolation() -> None:
    alice_headers = {"Authorization": "Bearer test-user-alice"}
    bob_headers = {"Authorization": "Bearer test-user-bob"}

    async with managed_config_client() as client:
        # Alice creates a configuration
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Alice Private Preset",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                    }
                ],
            },
            headers=alice_headers,
        )
        assert res.status_code == 201
        alice_cfg_id = res.json()["id"]

        # Bob attempts GET by ID -> 404 Not Found (NOT 403 to prevent enumeration)
        bob_get = await client.get(
            f"/api/v1/evaluation-configs/{alice_cfg_id}",
            headers=bob_headers,
        )
        assert bob_get.status_code == 404

        # Bob attempts PATCH -> 404
        bob_patch = await client.patch(
            f"/api/v1/evaluation-configs/{alice_cfg_id}",
            json={"name": "Bob Hijack Attempt"},
            headers=bob_headers,
        )
        assert bob_patch.status_code == 404

        # Bob attempts DELETE -> 404
        bob_del = await client.delete(
            f"/api/v1/evaluation-configs/{alice_cfg_id}",
            headers=bob_headers,
        )
        assert bob_del.status_code == 404

        # Bob lists configurations -> receives empty list
        bob_list = await client.get(
            "/api/v1/evaluation-configs",
            headers=bob_headers,
        )
        assert bob_list.status_code == 200
        assert bob_list.json()["total"] == 0

        # Anonymous attempt -> 401 Unauthorized
        anon_get = await client.get(f"/api/v1/evaluation-configs/{alice_cfg_id}")
        assert anon_get.status_code == 401


# =========================================================================
# 5. Schema Validation & Error Handling Tests
# =========================================================================


@pytest.mark.asyncio
async def test_configuration_validation_errors() -> None:
    auth_headers = {"Authorization": "Bearer test-user-alice"}

    async with managed_config_client() as client:
        # 1. Empty evaluators list
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={"name": "Invalid Config", "evaluators": []},
            headers=auth_headers,
        )
        assert res.status_code == 422

        # 2. Negative weight
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Negative Weight",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                        "weight": -1.0,
                    }
                ],
            },
            headers=auth_headers,
        )
        assert res.status_code == 422

        # 3. Threshold > 1.0
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Invalid Threshold",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                        "threshold": 1.5,
                    }
                ],
            },
            headers=auth_headers,
        )
        assert res.status_code == 422

        # 4. Duplicate evaluator definition
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Duplicate Evaluators",
                "evaluators": [
                    {"evaluator_type": "factuality", "backend": "llm_judge"},
                    {"evaluator_type": "factuality", "backend": "llm_judge"},
                ],
            },
            headers=auth_headers,
        )
        assert res.status_code == 422
        assert "Duplicate evaluator definition" in res.text

        # 5. Unsupported backend combination (native does not support hallucination)
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Unsupported Backend",
                "evaluators": [
                    {"evaluator_type": "hallucination", "backend": "native"},
                ],
            },
            headers=auth_headers,
        )
        assert res.status_code == 422
        assert "Native backend does not support" in res.text

        # 6. All evaluators disabled
        res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "All Disabled",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                        "enabled": False,
                    }
                ],
            },
            headers=auth_headers,
        )
        assert res.status_code == 422
        assert "at least one enabled evaluator" in res.text.lower()


# =========================================================================
# 6. Weighted Aggregation Unit & Scoring Tests
# =========================================================================


def test_calculate_case_score_weighted_aggregation() -> None:
    # Metric 1: score 0.8, weight 3.0
    m1 = EvaluationMetric(
        metric_name="factuality",
        evaluator_type=EvaluatorType.FACTUALITY,
        score=0.8,
        passed=True,
        threshold=0.7,
        status=MetricStatus.SUCCESS,
    )
    # Metric 2: score 0.4, weight 1.0
    m2 = EvaluationMetric(
        metric_name="relevance",
        evaluator_type=EvaluatorType.RELEVANCE,
        score=0.4,
        passed=False,
        threshold=0.5,
        status=MetricStatus.THRESHOLD_FAILED,
    )

    weights = {"factuality": 3.0, "relevance": 1.0}
    # Expected: (0.8 * 3.0 + 0.4 * 1.0) / (3.0 + 1.0) =
    # (2.4 + 0.4) / 4.0 = 2.8 / 4.0 = 0.70
    score, passed = calculate_case_score([m1, m2], weights=weights)
    assert score == 0.7
    assert passed is False  # m2 failed threshold

    # Without weights (equal 1.0): (0.8 + 0.4) / 2.0 = 0.60
    unweighted_score, _ = calculate_case_score([m1, m2], weights=None)
    assert unweighted_score == 0.6


def test_calculate_case_score_excludes_error_and_unavailable_metrics() -> None:
    m1 = EvaluationMetric(
        metric_name="factuality",
        evaluator_type=EvaluatorType.FACTUALITY,
        score=0.9,
        passed=True,
        threshold=0.8,
        status=MetricStatus.SUCCESS,
    )
    # Metric 2 errored
    m2 = EvaluationMetric(
        metric_name="relevance",
        evaluator_type=EvaluatorType.RELEVANCE,
        score=None,
        passed=False,
        threshold=0.5,
        status=MetricStatus.ERROR,
        explanation="Connection timeout",
    )
    # Metric 3 unavailable
    m3 = EvaluationMetric(
        metric_name="consistency",
        evaluator_type=EvaluatorType.CONSISTENCY,
        score=None,
        passed=None,
        threshold=None,
        status=MetricStatus.UNAVAILABLE,
    )

    weights = {"factuality": 2.0, "relevance": 5.0, "consistency": 1.0}
    # Only m1 has a valid score; m2 and m3 are excluded from the denominator
    score, passed = calculate_case_score([m1, m2, m3], weights=weights)
    assert score == 0.9
    assert passed is False  # m2 had ERROR status


# =========================================================================
# 7. Run Snapshotting & Reproducibility Integration Tests
# =========================================================================


@pytest.mark.asyncio
async def test_evaluation_run_with_config_preset_and_snapshot_immutability() -> None:
    alice_headers = {"Authorization": "Bearer test-user-alice"}

    async with managed_config_client() as client:
        # 1. Create dataset and case
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Preset Test DS", "description": "Test dataset"},
            headers=alice_headers,
        )
        assert ds_res.status_code == 201
        dataset_id = ds_res.json()["id"]

        case_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "input": "Summarize the text.",
                "expected_output": "A concise summary.",
            },
            headers=alice_headers,
        )
        assert case_res.status_code == 201
        case_id = case_res.json()["id"]

        # 2. Create evaluation configuration preset with native instruction_following
        cfg_res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Native Instruction Preset",
                "description": "Deterministic instruction following preset",
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "name": "instr_native",
                        "threshold": 0.5,
                        "weight": 2.5,
                        "configuration": {},
                    }
                ],
            },
            headers=alice_headers,
        )
        assert cfg_res.status_code == 201
        cfg_data = cfg_res.json()
        config_id = cfg_data["id"]
        original_hash = cfg_data["snapshot_hash"]

        # 3. Submit evaluation run referencing config_id
        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": dataset_id,
                "config_id": config_id,
                "responses": [{"case_id": case_id, "response": "A concise summary."}],
            },
            headers=alice_headers,
        )
        assert run_res.status_code in (201, 202)
        run_data = run_res.json()
        run_id = run_data["id"]

        # Verify run response includes configuration snapshot metadata
        assert run_data["config_id"] == config_id
        assert run_data["config_version"] == 1
        assert run_data["config_snapshot_hash"] == original_hash

        # 4. Fetch completed run by ID
        get_run = await client.get(
            f"/api/v1/evaluations/runs/{run_id}",
            headers=alice_headers,
        )
        assert get_run.status_code == 200
        run_fetched = get_run.json()
        assert run_fetched["status"] == "completed"
        assert run_fetched["overall_score"] is not None
        assert run_fetched["config_id"] == config_id
        assert run_fetched["config_version"] == 1
        assert run_fetched["config_snapshot_hash"] == original_hash

        # 5. Mutate the configuration preset (create version 2)
        update_res = await client.patch(
            f"/api/v1/evaluation-configs/{config_id}",
            json={
                "name": "Native Instruction Preset v2",
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "name": "instr_native",
                        "threshold": 0.99,
                        "weight": 5.0,
                    }
                ],
            },
            headers=alice_headers,
        )
        assert update_res.status_code == 200
        v2_hash = update_res.json()["snapshot_hash"]
        assert v2_hash != original_hash

        # 6. Verify the existing run is IMMUTABLE: snapshot hash and version remain v1
        get_run_after = await client.get(
            f"/api/v1/evaluations/runs/{run_id}",
            headers=alice_headers,
        )
        assert get_run_after.status_code == 200
        run_immutable = get_run_after.json()
        assert run_immutable["config_id"] == config_id
        assert run_immutable["config_version"] == 1
        assert run_immutable["config_snapshot_hash"] == original_hash
        assert run_immutable["config_snapshot_hash"] != v2_hash

        # 7. Launch a second run referencing version 1 explicitly
        run_v1_explicit = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": dataset_id,
                "config_id": config_id,
                "config_version": 1,
                "responses": [{"case_id": case_id, "response": "A concise summary."}],
            },
            headers=alice_headers,
        )
        assert run_v1_explicit.status_code in (201, 202)
        assert run_v1_explicit.json()["config_version"] == 1
        assert run_v1_explicit.json()["config_snapshot_hash"] == original_hash


# =========================================================================
# 8. User Deletion Decoupling Tests
# =========================================================================


@pytest.mark.asyncio
async def test_user_deletion_decouples_evaluation_config() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user = User(external_auth_id="clerk_delete_test", email="delete_me@test.com")
        session.add(user)
        await session.flush()
        user_id = user.id

        cfg = EvaluationConfig(
            name="Orphanable Config",
            description=None,
            version=1,
            evaluators=[{"evaluator_type": "factuality", "backend": "llm_judge"}],
            snapshot_hash="dummy-hash-1234567890",
            owner_user_id=user_id,
        )
        session.add(cfg)
        await session.commit()
        cfg_id = cfg.id

        # Delete user
        await session.execute(text(f"DELETE FROM users WHERE id = '{user_id}'"))
        await session.commit()
        session.expire_all()

        # Verify config still exists and owner_user_id was set to NULL
        res = await session.get(EvaluationConfig, cfg_id)
        assert res is not None
        assert res.owner_user_id is None

        # Clean up
        await session.execute(
            text(
                "TRUNCATE TABLE users, evaluation_configs, "
                "evaluation_config_versions CASCADE"
            )
        )
        await session.commit()
    await engine.dispose()


# =========================================================================
# 9. Multi-Version Progression & Pagination Tests
# =========================================================================


@pytest.mark.asyncio
async def test_multi_version_progression_and_nonexistent_version_404() -> None:
    headers = {"Authorization": "Bearer test-user-versioning"}

    async with managed_config_client() as client:
        # Create v1
        create_res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Versioned Config",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                        "weight": 1.0,
                    }
                ],
            },
            headers=headers,
        )
        assert create_res.status_code == 201
        cfg_id = create_res.json()["id"]

        # Update -> v2
        await client.patch(
            f"/api/v1/evaluation-configs/{cfg_id}",
            json={
                "name": "Versioned Config v2",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                        "weight": 2.0,
                    }
                ],
            },
            headers=headers,
        )

        # Update -> v3
        await client.patch(
            f"/api/v1/evaluation-configs/{cfg_id}",
            json={
                "name": "Versioned Config v3",
                "evaluators": [
                    {
                        "evaluator_type": "factuality",
                        "backend": "llm_judge",
                        "weight": 3.0,
                    }
                ],
            },
            headers=headers,
        )

        # Fetch active -> v3
        active = await client.get(
            f"/api/v1/evaluation-configs/{cfg_id}", headers=headers
        )
        assert active.status_code == 200
        assert active.json()["version"] == 3
        assert active.json()["evaluators"][0]["weight"] == 3.0

        # Fetch v1
        v1 = await client.get(
            f"/api/v1/evaluation-configs/{cfg_id}?version=1", headers=headers
        )
        assert v1.status_code == 200
        assert v1.json()["version"] == 1
        assert v1.json()["evaluators"][0]["weight"] == 1.0

        # Fetch v2
        v2 = await client.get(
            f"/api/v1/evaluation-configs/{cfg_id}?version=2", headers=headers
        )
        assert v2.status_code == 200
        assert v2.json()["version"] == 2
        assert v2.json()["evaluators"][0]["weight"] == 2.0

        # Fetch non-existent version 99 -> 404
        v99 = await client.get(
            f"/api/v1/evaluation-configs/{cfg_id}?version=99", headers=headers
        )
        assert v99.status_code == 404


@pytest.mark.asyncio
async def test_configuration_pagination_and_ordering() -> None:
    headers = {"Authorization": "Bearer test-user-pagination"}

    async with managed_config_client() as client:
        # Create 5 configs
        for i in range(5):
            await client.post(
                "/api/v1/evaluation-configs",
                json={
                    "name": f"Config Preset {i}",
                    "evaluators": [
                        {"evaluator_type": "factuality", "backend": "llm_judge"}
                    ],
                },
                headers=headers,
            )

        # Page 1, size 2
        p1 = await client.get(
            "/api/v1/evaluation-configs?page=1&page_size=2", headers=headers
        )
        assert p1.status_code == 200
        p1_data = p1.json()
        assert p1_data["total"] == 5
        assert p1_data["page"] == 1
        assert p1_data["page_size"] == 2
        assert len(p1_data["items"]) == 2
        assert p1_data["items"][0]["name"] == "Config Preset 4"

        # Page 2, size 2
        p2 = await client.get(
            "/api/v1/evaluation-configs?page=2&page_size=2", headers=headers
        )
        assert p2.status_code == 200
        assert len(p2.json()["items"]) == 2

        # Page 3, size 2 (1 remaining item)
        p3 = await client.get(
            "/api/v1/evaluation-configs?page=3&page_size=2", headers=headers
        )
        assert p3.status_code == 200
        assert len(p3.json()["items"]) == 1


# =========================================================================
# 10. Run Creation with Invalid or Cross-Tenant Config Rejection
# =========================================================================


@pytest.mark.asyncio
async def test_run_with_cross_tenant_or_invalid_config() -> None:
    alice_headers = {"Authorization": "Bearer test-user-alice"}
    bob_headers = {"Authorization": "Bearer test-user-bob"}

    async with managed_config_client() as client:
        # Create Alice dataset
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Alice DS", "description": "DS"},
            headers=alice_headers,
        )
        alice_ds_id = ds_res.json()["id"]

        case_res = await client.post(
            f"/api/v1/datasets/{alice_ds_id}/cases",
            json={"input": "Hello", "expected_output": "Hi"},
            headers=alice_headers,
        )
        case_id = case_res.json()["id"]

        # Alice creates a configuration
        cfg_res = await client.post(
            "/api/v1/evaluation-configs",
            json={
                "name": "Alice Config",
                "evaluators": [
                    {"evaluator_type": "instruction_following", "backend": "native"}
                ],
            },
            headers=alice_headers,
        )
        alice_cfg_id = cfg_res.json()["id"]

        # 1. Alice creates Bob dataset
        bob_ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Bob DS", "description": "DS"},
            headers=bob_headers,
        )
        bob_ds_id = bob_ds_res.json()["id"]

        bob_case_res = await client.post(
            f"/api/v1/datasets/{bob_ds_id}/cases",
            json={"input": "Hello Bob", "expected_output": "Hi Bob"},
            headers=bob_headers,
        )
        bob_case_id = bob_case_res.json()["id"]

        # Bob attempts to launch a run using Alice's config_id -> 404
        cross_run = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": bob_ds_id,
                "config_id": alice_cfg_id,
                "responses": [{"case_id": bob_case_id, "response": "Hi Bob"}],
            },
            headers=bob_headers,
        )
        assert cross_run.status_code == 404
        assert "not found" in cross_run.json()["detail"].lower()

        # Non-existent config_id -> 404
        bad_run = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": alice_ds_id,
                "config_id": str(uuid.uuid4()),
                "responses": [{"case_id": case_id, "response": "Hi"}],
            },
            headers=alice_headers,
        )
        assert bad_run.status_code == 404

        # Non-existent config_version -> 404
        bad_ver_run = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": alice_ds_id,
                "config_id": alice_cfg_id,
                "config_version": 999,
                "responses": [{"case_id": case_id, "response": "Hi"}],
            },
            headers=alice_headers,
        )
        assert bad_ver_run.status_code == 404
