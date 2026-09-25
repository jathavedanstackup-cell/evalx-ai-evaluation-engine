import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_auth_provider
from app.auth.test_provider import TestAuthProvider, make_test_auth_headers
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.evaluation.lifecycle import RunStatus
from app.evaluation.snapshot import compute_gate_snapshot
from app.main import app
from app.models.dataset import Dataset
from app.models.evaluation import Evaluation
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.schemas.regression_gate import (
    GateOperator,
    GateOverallStatus,
    GateRuleInput,
    GateSeverity,
    RegressionGateCreate,
    RuleEvaluationStatus,
)
from app.services.regression_gate_service import _compare_op
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_gate_client(
    default_user_id: str | None = "gate-user-1",
) -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient for testing regression gates and experiments."""
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
                    "evaluation_config_versions, regression_gates, "
                    "regression_gate_versions, regression_gate_evaluations, "
                    "experiments CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


async def _seed_completed_run(
    session: AsyncSession,
    dataset: Dataset,
    user_id: uuid.UUID,
    case_results: list[tuple[uuid.UUID, float, bool, dict[str, float]]],
    overall_score: float,
    metrics_summary: dict[str, Any] | None = None,
    run_status: str = RunStatus.COMPLETED.value,
) -> EvaluationRun:
    """Helper to seed an evaluation run and results in the database."""
    evaluation = Evaluation(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        name=f"Eval-{uuid.uuid4().hex[:6]}",
        model_provider="openai",
        model_name="gpt-4o",
        created_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    session.add(evaluation)
    await session.flush()

    run = EvaluationRun(
        id=uuid.uuid4(),
        evaluation_id=evaluation.id,
        dataset_version=1,
        dataset_snapshot_hash="gate-test-snapshot-hash",
        status=run_status,
        started_at=datetime.now(UTC) - timedelta(seconds=30),
        completed_at=datetime.now(UTC)
        if run_status == RunStatus.COMPLETED.value
        else None,
        duration_ms=30000.0,
        correlation_id=f"cid-{uuid.uuid4().hex[:8]}",
        overall_score=overall_score,
        total_cases=len(case_results),
        completed_cases=len(case_results)
        if run_status == RunStatus.COMPLETED.value
        else 0,
        failed_cases=0,
        metrics_summary=metrics_summary or {},
        created_at=datetime.now(UTC) - timedelta(seconds=35),
    )
    session.add(run)
    await session.flush()

    for case_id, score, passed, metrics_dict in case_results:
        metric_objs = {
            m_name: {
                "score": m_score,
                "status": "passed" if passed else "failed",
                "threshold": 0.5,
                "reasoning": "Gate test reasoning",
            }
            for m_name, m_score in metrics_dict.items()
        }
        res = EvaluationResult(
            id=uuid.uuid4(),
            run_id=run.id,
            case_id=case_id,
            overall_score=score,
            passed=passed,
            metrics=metric_objs,
            execution_time_ms=100.0,
            created_at=datetime.now(UTC),
        )
        session.add(res)

    await session.commit()
    await session.refresh(run)
    return run


# =========================================================================
# 1. Snapshot Computation & Operator Logic Tests
# =========================================================================


def test_gate_snapshot_deterministic() -> None:
    """Snapshot calculation is deterministic irrespective of rule input ordering."""
    rules_order_1 = [
        {
            "metric_name": "relevance",
            "operator": "gte",
            "threshold": 0.7,
            "is_delta": False,
            "severity": "critical",
            "enabled": True,
        },
        {
            "metric_name": "factuality",
            "operator": "gte",
            "threshold": 0.8,
            "is_delta": True,
            "severity": "critical",
            "enabled": True,
        },
    ]
    rules_order_2 = [
        {
            "metric_name": "factuality",
            "operator": "gte",
            "threshold": 0.8,
            "is_delta": True,
            "severity": "critical",
            "enabled": True,
        },
        {
            "metric_name": "relevance",
            "operator": "gte",
            "threshold": 0.7,
            "is_delta": False,
            "severity": "critical",
            "enabled": True,
        },
    ]

    snap_1, hash_1 = compute_gate_snapshot("My Gate", "Desc", 1, rules_order_1)
    snap_2, hash_2 = compute_gate_snapshot("My Gate", "Desc", 1, rules_order_2)

    assert hash_1 == hash_2
    assert len(hash_1) == 64
    assert snap_1["rules"] == snap_2["rules"]


def test_gate_operator_evaluations() -> None:
    """Validation of comparison operators with floating-point tolerance."""
    assert _compare_op(0.85, GateOperator.GTE, 0.85) is True
    assert _compare_op(0.8499, GateOperator.GTE, 0.85) is False
    assert _compare_op(0.851, GateOperator.GT, 0.85) is True
    assert _compare_op(0.85, GateOperator.GT, 0.85) is False
    assert _compare_op(0.85, GateOperator.LTE, 0.85) is True
    assert _compare_op(0.851, GateOperator.LTE, 0.85) is False
    assert _compare_op(0.849, GateOperator.LT, 0.85) is True
    assert _compare_op(0.85, GateOperator.LT, 0.85) is False
    assert _compare_op(0.8500001, GateOperator.EQ, 0.8500002) is True
    assert _compare_op(0.85, GateOperator.EQ, 0.86) is False


def test_gate_schema_duplicate_rule_rejected() -> None:
    """Duplicate rule definition for same metric/op/is_delta must raise error."""
    rules = [
        GateRuleInput(
            metric_name="factuality",
            operator=GateOperator.GTE,
            threshold=0.8,
            is_delta=False,
        ),
        GateRuleInput(
            metric_name="factuality",
            operator=GateOperator.GTE,
            threshold=0.9,
            is_delta=False,
        ),
    ]
    with pytest.raises(ValueError, match="Duplicate rule detected"):
        RegressionGateCreate(name="Duplicate Gate", rules=rules)


def test_gate_schema_all_disabled_rejected() -> None:
    """Gate with no enabled rules must raise ValueError."""
    rules = [
        GateRuleInput(
            metric_name="factuality",
            operator=GateOperator.GTE,
            threshold=0.8,
            enabled=False,
        ),
    ]
    with pytest.raises(ValueError, match="at least one enabled rule"):
        RegressionGateCreate(name="All Disabled Gate", rules=rules)


# =========================================================================
# 2. Regression Gate CRUD & Versioning API Tests
# =========================================================================


@pytest.mark.asyncio
async def test_gate_lifecycle_crud_and_versioning() -> None:
    """Full lifecycle: create gate v1, inspect, update to v2, list versions, delete."""
    async with managed_gate_client(default_user_id="gate-crud-user") as client:
        auth_headers = make_test_auth_headers("gate-crud-user")

        # 1. Create gate v1
        create_payload = {
            "name": "Production Gate",
            "description": "Standard release gate",
            "rules": [
                {
                    "metric_name": "factuality",
                    "operator": "gte",
                    "threshold": 0.8,
                    "is_delta": False,
                    "severity": "critical",
                    "enabled": True,
                },
                {
                    "metric_name": "overall_score",
                    "operator": "gte",
                    "threshold": -0.05,
                    "is_delta": True,
                    "severity": "critical",
                    "enabled": True,
                },
            ],
        }
        res = await client.post(
            "/api/v1/regression-gates", json=create_payload, headers=auth_headers
        )
        assert res.status_code == 201
        gate_v1 = res.json()
        gate_id = gate_v1["id"]
        assert gate_v1["name"] == "Production Gate"
        assert gate_v1["version"] == 1
        assert len(gate_v1["rules"]) == 2
        assert len(gate_v1["snapshot_hash"]) == 64

        # 2. Get gate by ID
        res = await client.get(
            f"/api/v1/regression-gates/{gate_id}", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["id"] == gate_id

        # 3. Update gate -> v2
        update_payload = {
            "description": "Updated release gate for v2",
            "rules": [
                {
                    "metric_name": "factuality",
                    "operator": "gte",
                    "threshold": 0.85,
                    "is_delta": False,
                    "severity": "critical",
                    "enabled": True,
                }
            ],
        }
        res = await client.patch(
            f"/api/v1/regression-gates/{gate_id}",
            json=update_payload,
            headers=auth_headers,
        )
        assert res.status_code == 200
        gate_v2 = res.json()
        assert gate_v2["version"] == 2
        assert gate_v2["description"] == "Updated release gate for v2"
        assert len(gate_v2["rules"]) == 1
        assert gate_v2["rules"][0]["threshold"] == 0.85

        # 4. List versions
        res = await client.get(
            f"/api/v1/regression-gates/{gate_id}/versions", headers=auth_headers
        )
        assert res.status_code == 200
        versions = res.json()
        assert len(versions) == 2
        assert versions[0]["version"] == 1
        assert versions[1]["version"] == 2

        # 5. Get specific historical version 1
        res = await client.get(
            f"/api/v1/regression-gates/{gate_id}/versions/1", headers=auth_headers
        )
        assert res.status_code == 200
        v1_data = res.json()
        assert v1_data["version"] == 1
        assert len(v1_data["rules"]) == 2
        assert v1_data["rules"][0]["threshold"] == 0.8

        # 6. List gates with pagination
        res = await client.get(
            "/api/v1/regression-gates?page=1&page_size=10", headers=auth_headers
        )
        assert res.status_code == 200
        list_data = res.json()
        assert list_data["total"] == 1
        assert len(list_data["items"]) == 1

        # 7. Delete gate
        res = await client.delete(
            f"/api/v1/regression-gates/{gate_id}", headers=auth_headers
        )
        assert res.status_code == 204

        # Verify 404 after deletion
        res = await client.get(
            f"/api/v1/regression-gates/{gate_id}", headers=auth_headers
        )
        assert res.status_code == 404


# =========================================================================
# 3. Gate Evaluation Tests (Absolute & Delta Rules, Determinism)
# =========================================================================


@pytest.mark.asyncio
async def test_gate_evaluate_absolute_rules_pass_and_fail() -> None:
    """Gate evaluation with absolute criteria against a completed target run."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_gate_client(default_user_id="eval-abs-user") as client:
        auth_headers = make_test_auth_headers("eval-abs-user")

        # 1. Create dataset and cases
        ds_res = await client.post(
            "/api/v1/datasets", json={"name": "Eval DS"}, headers=auth_headers
        )
        ds_id = uuid.UUID(ds_res.json()["id"])
        user_id = uuid.UUID(ds_res.json()["owner_user_id"])

        c1 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "Case 1"},
                headers=auth_headers,
            )
        ).json()
        c1_id = uuid.UUID(c1["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, ds_id)
            assert dataset is not None

            # Seed target run with factuality=0.88, relevance=0.65, overall_score=0.765
            target_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [(c1_id, 0.765, True, {"factuality": 0.88, "relevance": 0.65})],
                0.765,
                {"metric_averages": {"factuality": 0.88, "relevance": 0.65}},
            )

        # 2. Create Gate that should PASS (factuality >= 0.80, overall_score >= 0.70)
        pass_gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Passing Gate",
                "rules": [
                    {
                        "metric_name": "factuality",
                        "operator": "gte",
                        "threshold": 0.80,
                        "is_delta": False,
                    },
                    {
                        "metric_name": "overall_score",
                        "operator": "gte",
                        "threshold": 0.70,
                        "is_delta": False,
                    },
                ],
            },
            headers=auth_headers,
        )
        pass_gate_id = pass_gate_res.json()["id"]

        # Evaluate passing gate
        eval_res = await client.post(
            f"/api/v1/regression-gates/{pass_gate_id}/evaluate",
            json={"target_run_id": str(target_run.id)},
            headers=auth_headers,
        )
        assert eval_res.status_code == 200
        eval_data = eval_res.json()
        assert eval_data["overall_status"] == GateOverallStatus.PASS.value
        assert len(eval_data["rule_results"]) == 2
        assert all(
            r["status"] == RuleEvaluationStatus.PASS.value
            for r in eval_data["rule_results"]
        )

        # 3. Create Gate that should FAIL (relevance >= 0.80, but actual is 0.65)
        fail_gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Failing Gate",
                "rules": [
                    {
                        "metric_name": "factuality",
                        "operator": "gte",
                        "threshold": 0.80,
                        "is_delta": False,
                    },
                    {
                        "metric_name": "relevance",
                        "operator": "gte",
                        "threshold": 0.80,
                        "is_delta": False,
                    },
                ],
            },
            headers=auth_headers,
        )
        fail_gate_id = fail_gate_res.json()["id"]

        eval_fail_res = await client.post(
            f"/api/v1/regression-gates/{fail_gate_id}/evaluate",
            json={"target_run_id": str(target_run.id)},
            headers=auth_headers,
        )
        assert eval_fail_res.status_code == 200
        fail_data = eval_fail_res.json()
        assert fail_data["overall_status"] == GateOverallStatus.FAIL.value
        relevance_rule = next(
            r for r in fail_data["rule_results"] if r["metric_name"] == "relevance"
        )
        assert relevance_rule["status"] == RuleEvaluationStatus.FAIL.value
        assert relevance_rule["target_value"] == 0.65

        # 4. Inconclusive rule (evaluating metric not present in run summary)
        inconclusive_gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Inconclusive Gate",
                "rules": [
                    {
                        "metric_name": "non_existent_metric",
                        "operator": "gte",
                        "threshold": 0.5,
                        "is_delta": False,
                    },
                ],
            },
            headers=auth_headers,
        )
        inconclusive_gate_id = inconclusive_gate_res.json()["id"]

        eval_inc_res = await client.post(
            f"/api/v1/regression-gates/{inconclusive_gate_id}/evaluate",
            json={"target_run_id": str(target_run.id)},
            headers=auth_headers,
        )
        assert eval_inc_res.status_code == 200
        inc_data = eval_inc_res.json()
        assert inc_data["overall_status"] == GateOverallStatus.INCONCLUSIVE.value
        assert (
            inc_data["rule_results"][0]["status"]
            == RuleEvaluationStatus.INCONCLUSIVE.value
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_gate_evaluate_delta_rules_against_baseline() -> None:
    """Test delta criteria comparing target run against a baseline run."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_gate_client(default_user_id="eval-delta-user") as client:
        auth_headers = make_test_auth_headers("eval-delta-user")

        ds_res = await client.post(
            "/api/v1/datasets", json={"name": "Delta DS"}, headers=auth_headers
        )
        ds_id = uuid.UUID(ds_res.json()["id"])
        user_id = uuid.UUID(ds_res.json()["owner_user_id"])

        c1 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "Delta Case 1"},
                headers=auth_headers,
            )
        ).json()
        c1_id = uuid.UUID(c1["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, ds_id)
            assert dataset is not None

            # Base run: factuality=0.90, overall_score=0.90
            base_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [(c1_id, 0.90, True, {"factuality": 0.90})],
                0.90,
                {"metric_averages": {"factuality": 0.90}},
            )

            # Target run: factuality=0.82 (delta -0.08), overall=0.82 (delta -0.08)
            target_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [(c1_id, 0.82, True, {"factuality": 0.82})],
                0.82,
                {"metric_averages": {"factuality": 0.82}},
            )

        # Gate with allowable regression of at most -0.05 (delta >= -0.05)
        # Since actual delta is -0.08, this MUST FAIL
        gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Regression Tolerance Gate",
                "rules": [
                    {
                        "metric_name": "factuality",
                        "operator": "gte",
                        "threshold": -0.05,
                        "is_delta": True,
                        "severity": "critical",
                    }
                ],
            },
            headers=auth_headers,
        )
        gate_id = gate_res.json()["id"]

        eval_res = await client.post(
            f"/api/v1/regression-gates/{gate_id}/evaluate",
            json={
                "target_run_id": str(target_run.id),
                "baseline_run_id": str(base_run.id),
            },
            headers=auth_headers,
        )
        assert eval_res.status_code == 200
        eval_data = eval_res.json()
        assert eval_data["overall_status"] == GateOverallStatus.FAIL.value
        rule_res = eval_data["rule_results"][0]
        assert rule_res["status"] == RuleEvaluationStatus.FAIL.value
        assert rule_res["delta"] == -0.08
        assert rule_res["target_value"] == 0.82
        assert rule_res["baseline_value"] == 0.90

        # Now test delta without baseline -> INCONCLUSIVE
        eval_no_base_res = await client.post(
            f"/api/v1/regression-gates/{gate_id}/evaluate",
            json={"target_run_id": str(target_run.id)},
            headers=auth_headers,
        )
        assert eval_no_base_res.status_code == 200
        no_base_data = eval_no_base_res.json()
        assert no_base_data["overall_status"] == GateOverallStatus.INCONCLUSIVE.value
        assert (
            no_base_data["rule_results"][0]["status"]
            == RuleEvaluationStatus.INCONCLUSIVE.value
        )

        # Test listing evaluations
        list_eval_res = await client.get(
            f"/api/v1/regression-gates/{gate_id}/evaluations",
            headers=auth_headers,
        )
        assert list_eval_res.status_code == 200
        evals_list = list_eval_res.json()
        assert evals_list["total"] == 2
        assert len(evals_list["items"]) == 2

        # Test fetching specific evaluation result
        eval_id = eval_data["id"]
        single_eval_res = await client.get(
            f"/api/v1/regression-gates/evaluations/{eval_id}",
            headers=auth_headers,
        )
        assert single_eval_res.status_code == 200
        assert single_eval_res.json()["id"] == eval_id

    await engine.dispose()


# =========================================================================
# 4. Error Handling, Lifecycle Guards & Multi-Tenancy Tests
# =========================================================================


@pytest.mark.asyncio
async def test_gate_evaluation_lifecycle_guard_conflict() -> None:
    """Target run or baseline run that is not COMPLETED must return 409 Conflict."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_gate_client(default_user_id="guard-user") as client:
        auth_headers = make_test_auth_headers("guard-user")

        ds_res = await client.post(
            "/api/v1/datasets", json={"name": "Guard DS"}, headers=auth_headers
        )
        ds_id = uuid.UUID(ds_res.json()["id"])
        user_id = uuid.UUID(ds_res.json()["owner_user_id"])

        c1 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "Case 1"},
                headers=auth_headers,
            )
        ).json()
        c1_id = uuid.UUID(c1["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, ds_id)
            assert dataset is not None

            # Pending target run
            pending_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [(c1_id, 0.8, True, {"factuality": 0.8})],
                0.8,
                run_status=RunStatus.PENDING.value,
            )

        gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Guard Gate",
                "rules": [
                    {"metric_name": "factuality", "operator": "gte", "threshold": 0.8}
                ],
            },
            headers=auth_headers,
        )
        gate_id = gate_res.json()["id"]

        # Evaluating pending run -> 409 Conflict
        eval_res = await client.post(
            f"/api/v1/regression-gates/{gate_id}/evaluate",
            json={"target_run_id": str(pending_run.id)},
            headers=auth_headers,
        )
        assert eval_res.status_code == 409
        assert "not completed" in eval_res.json()["detail"].lower()

    await engine.dispose()


@pytest.mark.asyncio
async def test_gate_tenant_isolation_returns_404() -> None:
    """Accessing another tenant's gate returns 404 (non-enumerable multi-tenancy)."""
    async with managed_gate_client(default_user_id="tenant-a") as client:
        headers_a = make_test_auth_headers("tenant-a")
        headers_b = make_test_auth_headers("tenant-b")

        # Tenant A creates gate
        gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Tenant A Gate",
                "rules": [
                    {"metric_name": "factuality", "operator": "gte", "threshold": 0.8}
                ],
            },
            headers=headers_a,
        )
        gate_id = gate_res.json()["id"]

        # Tenant B attempts to read gate -> 404
        res = await client.get(f"/api/v1/regression-gates/{gate_id}", headers=headers_b)
        assert res.status_code == 404

        # Tenant B attempts to update gate -> 404
        res = await client.patch(
            f"/api/v1/regression-gates/{gate_id}",
            json={"description": "Hacked"},
            headers=headers_b,
        )
        assert res.status_code == 404

        # Tenant B attempts to delete gate -> 404
        res = await client.delete(
            f"/api/v1/regression-gates/{gate_id}", headers=headers_b
        )
        assert res.status_code == 404


# =========================================================================
# 5. Evaluation Experiments CRUD & Scoping Tests
# =========================================================================


@pytest.mark.asyncio
async def test_experiments_crud_and_isolation() -> None:
    """Full lifecycle: create experiment, read, update, list, delete, isolation."""
    async with managed_gate_client(default_user_id="exp-user") as client:
        auth_headers = make_test_auth_headers("exp-user")
        headers_other = make_test_auth_headers("other-exp-user")

        # 1. Create experiment
        create_payload = {
            "name": "Model Prompt Optimization Exp",
            "description": "Evaluating prompt v2 iterations",
        }
        res = await client.post(
            "/api/v1/experiments", json=create_payload, headers=auth_headers
        )
        assert res.status_code == 201
        exp_data = res.json()
        exp_id = exp_data["id"]
        assert exp_data["name"] == "Model Prompt Optimization Exp"
        assert exp_data["status"] == "active"

        # 2. Get experiment
        res = await client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["id"] == exp_id

        # 3. Update experiment
        res = await client.patch(
            f"/api/v1/experiments/{exp_id}",
            json={"status": "completed", "description": "Finished prompt tuning"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["status"] == "completed"
        assert updated["description"] == "Finished prompt tuning"

        # 4. List experiments with filtering
        res = await client.get(
            "/api/v1/experiments?status=completed", headers=auth_headers
        )
        assert res.status_code == 200
        list_data = res.json()
        assert list_data["total"] == 1
        assert list_data["items"][0]["id"] == exp_id

        # 5. Cross-tenant read -> 404
        res = await client.get(f"/api/v1/experiments/{exp_id}", headers=headers_other)
        assert res.status_code == 404

        # 6. Delete experiment
        res = await client.delete(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
        assert res.status_code == 204

        # Verify 404
        res = await client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
        assert res.status_code == 404


# =========================================================================
# 6. Warning Severity, Disabled Rules & Validation Tests
# =========================================================================


@pytest.mark.asyncio
async def test_gate_warning_severity_and_disabled_rules() -> None:
    """Warning failure does NOT fail gate if critical rules pass. Disabled = SKIPPED."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_gate_client(default_user_id="warn-user") as client:
        auth_headers = make_test_auth_headers("warn-user")

        ds_res = await client.post(
            "/api/v1/datasets", json={"name": "Warn DS"}, headers=auth_headers
        )
        ds_id = uuid.UUID(ds_res.json()["id"])
        user_id = uuid.UUID(ds_res.json()["owner_user_id"])

        c1 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "Warn Case"},
                headers=auth_headers,
            )
        ).json()
        c1_id = uuid.UUID(c1["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, ds_id)
            assert dataset is not None

            # Seed target run: factuality=0.85, latency_metric=0.40, overall_score=0.85
            target_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [(c1_id, 0.85, True, {"factuality": 0.85, "latency_score": 0.40})],
                0.85,
                {"metric_averages": {"factuality": 0.85, "latency_score": 0.40}},
            )

        # Gate with:
        # 1. Critical rule: factuality >= 0.80 (PASSES)
        # 2. Warning rule: latency_score >= 0.80 (FAILS, but only warning!)
        # 3. Disabled rule: overall_score >= 0.99 (SKIPPED)
        gate_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Warning Severity Gate",
                "rules": [
                    {
                        "metric_name": "factuality",
                        "operator": "gte",
                        "threshold": 0.80,
                        "severity": "critical",
                        "enabled": True,
                    },
                    {
                        "metric_name": "latency_score",
                        "operator": "gte",
                        "threshold": 0.80,
                        "severity": "warning",
                        "enabled": True,
                    },
                    {
                        "metric_name": "overall_score",
                        "operator": "gte",
                        "threshold": 0.99,
                        "severity": "critical",
                        "enabled": False,
                    },
                ],
            },
            headers=auth_headers,
        )
        gate_id = gate_res.json()["id"]

        eval_res = await client.post(
            f"/api/v1/regression-gates/{gate_id}/evaluate",
            json={"target_run_id": str(target_run.id)},
            headers=auth_headers,
        )
        assert eval_res.status_code == 200
        eval_data = eval_res.json()

        # Overall status MUST be PASS because the only critical rule passed!
        assert eval_data["overall_status"] == GateOverallStatus.PASS.value

        rule_map = {r["metric_name"]: r for r in eval_data["rule_results"]}
        assert rule_map["factuality"]["status"] == RuleEvaluationStatus.PASS.value
        assert rule_map["latency_score"]["status"] == RuleEvaluationStatus.FAIL.value
        assert rule_map["latency_score"]["severity"] == GateSeverity.WARNING.value
        assert rule_map["overall_score"]["status"] == RuleEvaluationStatus.SKIPPED.value

    await engine.dispose()


@pytest.mark.asyncio
async def test_gate_search_and_validation_errors() -> None:
    """Test gate search filtering and 404 validation for unknown config and runs."""
    async with managed_gate_client(default_user_id="search-user") as client:
        auth_headers = make_test_auth_headers("search-user")
        random_uuid = str(uuid.uuid4())

        # Create two gates
        await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Alpha Safety Gate",
                "rules": [
                    {"metric_name": "factuality", "operator": "gte", "threshold": 0.8}
                ],
            },
            headers=auth_headers,
        )
        await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Beta Performance Gate",
                "rules": [
                    {"metric_name": "factuality", "operator": "gte", "threshold": 0.7}
                ],
            },
            headers=auth_headers,
        )

        # Search for "Alpha"
        search_res = await client.get(
            "/api/v1/regression-gates?search=Alpha", headers=auth_headers
        )
        assert search_res.status_code == 200
        search_data = search_res.json()
        assert search_data["total"] == 1
        assert search_data["items"][0]["name"] == "Alpha Safety Gate"

        # Create gate with non-existent configuration_id -> 404
        bad_cfg_res = await client.post(
            "/api/v1/regression-gates",
            json={
                "name": "Invalid Config Gate",
                "configuration_id": random_uuid,
                "rules": [
                    {"metric_name": "factuality", "operator": "gte", "threshold": 0.8}
                ],
            },
            headers=auth_headers,
        )
        assert bad_cfg_res.status_code == 404

        # Create experiment with non-existent configuration_id -> 404
        bad_exp_cfg = await client.post(
            "/api/v1/experiments",
            json={
                "name": "Invalid Exp",
                "configuration_id": random_uuid,
            },
            headers=auth_headers,
        )
        assert bad_exp_cfg.status_code == 404

        # Create experiment with non-existent baseline_run_id -> 404
        bad_exp_run = await client.post(
            "/api/v1/experiments",
            json={
                "name": "Invalid Exp 2",
                "baseline_run_id": random_uuid,
            },
            headers=auth_headers,
        )
        assert bad_exp_run.status_code == 404
