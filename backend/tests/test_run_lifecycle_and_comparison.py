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
from app.evaluation.errors import InvalidRunStateTransitionError
from app.evaluation.lifecycle import RunStatus, validate_run_transition
from app.main import app
from app.models.dataset import Dataset, DatasetCase
from app.models.evaluation import Evaluation
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.services import evaluation_run_service
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_auth_client(
    default_user_id: str | None = "test-user-1",
    executor_override: object | None = None,
) -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient configured for testing run lifecycle and comparison."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_provider = TestAuthProvider(default_user_id=default_user_id)
    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: test_provider
    if executor_override is not None:
        app.dependency_overrides[get_run_executor] = lambda: executor_override
    else:
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
# 1. Lifecycle Unit Tests (RunStatus & Transitions)
# =========================================================================


def test_cancelled_status_transitions() -> None:
    """Verifies that CANCELLED transitions comply with the lifecycle state machine."""
    # Valid transitions
    assert (
        validate_run_transition(RunStatus.PENDING, RunStatus.CANCELLED)
        == RunStatus.CANCELLED
    )
    assert (
        validate_run_transition(RunStatus.RUNNING, RunStatus.CANCELLED)
        == RunStatus.CANCELLED
    )

    # Terminal transitions from CANCELLED must fail
    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.CANCELLED, RunStatus.RUNNING)
    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.CANCELLED, RunStatus.COMPLETED)
    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.CANCELLED, RunStatus.FAILED)

    # Cannot cancel already completed or failed runs
    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.COMPLETED, RunStatus.CANCELLED)
    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.FAILED, RunStatus.CANCELLED)


# =========================================================================
# 2. Cancellation API & Service Tests
# =========================================================================


class PendingRunExecutor(SynchronousRunExecutor):
    """Test executor that records run in PENDING status without executing."""

    async def submit_and_execute(
        self,
        session: AsyncSession,
        data: object,
        owner_user_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        evaluator_overrides: object = None,
    ) -> EvaluationRun:
        run = await evaluation_run_service.submit_evaluation_run(
            session=session,
            data=data,  # type: ignore[arg-type]
            owner_user_id=owner_user_id,
            correlation_id=correlation_id,
        )
        return run


@pytest.mark.asyncio
async def test_cancel_pending_run_api() -> None:
    """Cancelling a PENDING run marks it CANCELLED and emits event."""
    async with managed_auth_client(
        default_user_id="user-pending",
        executor_override=PendingRunExecutor(),
    ) as client:
        # Create dataset and test case
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Cancel Dataset"},
            headers=make_test_auth_headers("user-pending"),
        )
        assert ds_res.status_code == 201
        ds_id = ds_res.json()["id"]

        case_res = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Hello", "expected_output": "World"},
            headers=make_test_auth_headers("user-pending"),
        )
        assert case_res.status_code == 201

        # Launch run (will remain PENDING due to PendingRunExecutor)
        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": ds_id,
                "evaluators": [{"evaluator_type": "factuality", "backend": "native"}],
            },
            headers={
                **make_test_auth_headers("user-pending"),
                "X-Correlation-ID": "cid-cancel-pending",
            },
        )
        assert run_res.status_code == 201
        run_id = run_res.json()["id"]
        assert run_res.json()["status"] == "pending"

        # Cancel the pending run
        cancel_res = await client.post(
            f"/api/v1/evaluations/runs/{run_id}/cancel",
            headers={
                **make_test_auth_headers("user-pending"),
                "X-Correlation-ID": "cid-cancel-exec",
            },
        )
        assert cancel_res.status_code == 200
        cancel_data = cancel_res.json()
        assert cancel_data["id"] == run_id
        assert cancel_data["status"] == "cancelled"
        assert cancel_data["completed_at"] is not None

        # Fetch the run to confirm persistence
        get_res = await client.get(
            f"/api/v1/evaluations/runs/{run_id}",
            headers=make_test_auth_headers("user-pending"),
        )
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_terminal_run_returns_409() -> None:
    """Attempting to cancel already completed/cancelled run returns 409."""
    async with managed_auth_client(default_user_id="user-completed") as client:
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Completed Dataset"},
            headers=make_test_auth_headers("user-completed"),
        )
        ds_id = ds_res.json()["id"]

        case_res = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Hello", "expected_output": "World"},
            headers=make_test_auth_headers("user-completed"),
        )
        case_id = case_res.json()["id"]

        # Launch run synchronously (will reach COMPLETED)
        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": ds_id,
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                        "threshold": 1.0,
                    }
                ],
                "responses": [{"case_id": case_id, "response": "World"}],
            },
            headers=make_test_auth_headers("user-completed"),
        )
        assert run_res.status_code == 201
        run_id = run_res.json()["id"]
        assert run_res.json()["status"] == "completed"

        # Try to cancel completed run -> 409
        cancel_res = await client.post(
            f"/api/v1/evaluations/runs/{run_id}/cancel",
            headers=make_test_auth_headers("user-completed"),
        )
        assert cancel_res.status_code == 409
        assert "terminal state 'completed'" in cancel_res.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_cross_tenant_returns_404() -> None:
    """User B cannot cancel User A's run; returns 404 (IDOR protection)."""
    async with managed_auth_client(
        default_user_id="user-a",
        executor_override=PendingRunExecutor(),
    ) as client:
        # User A creates dataset and run
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "User A Dataset"},
            headers=make_test_auth_headers("user-a"),
        )
        ds_id = ds_res.json()["id"]

        await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Question A"},
            headers=make_test_auth_headers("user-a"),
        )

        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": ds_id,
                "evaluators": [{"evaluator_type": "factuality", "backend": "native"}],
            },
            headers=make_test_auth_headers("user-a"),
        )
        run_id = run_res.json()["id"]

        # User B attempts to cancel User A's run
        cancel_res = await client.post(
            f"/api/v1/evaluations/runs/{run_id}/cancel",
            headers=make_test_auth_headers("user-b"),
        )
        assert cancel_res.status_code == 404
        assert "not found" in cancel_res.json()["detail"].lower()


# =========================================================================
# 3. Timeout Reaper Tests
# =========================================================================


@pytest.mark.asyncio
async def test_reap_stale_runs_endpoint_and_service() -> None:
    """Reaping stale runs marks only expired RUNNING runs as FAILED."""
    async with managed_auth_client(
        default_user_id="reaper-user",
        executor_override=PendingRunExecutor(),
    ) as client:
        # Create user and dataset
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Reaper Dataset"},
            headers=make_test_auth_headers("reaper-user"),
        )
        ds_id = ds_res.json()["id"]

        await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Reap test case"},
            headers=make_test_auth_headers("reaper-user"),
        )

        run_res = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": ds_id,
                "evaluators": [{"evaluator_type": "factuality", "backend": "native"}],
            },
            headers=make_test_auth_headers("reaper-user"),
        )
        run_id = run_res.json()["id"]

        # Manually alter the run to be RUNNING and started 2 hours ago
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db_session:
            run_db = await db_session.get(EvaluationRun, uuid.UUID(run_id))
            assert run_db is not None
            run_db.status = RunStatus.RUNNING.value
            run_db.started_at = datetime.now(UTC) - timedelta(seconds=7200)
            await db_session.commit()

        await engine.dispose()

        # Call reap endpoint with timeout 1800s
        reap_res = await client.post(
            "/api/v1/evaluations/runs/maintenance/reap-stale?timeout_seconds=1800",
            headers=make_test_auth_headers("reaper-user"),
        )
        assert reap_res.status_code == 200
        reap_data = reap_res.json()
        assert reap_data["reaped_count"] == 1
        assert run_id in reap_data["reaped_run_ids"]

        # Verify the run status is now FAILED with timeout error message
        get_res = await client.get(
            f"/api/v1/evaluations/runs/{run_id}",
            headers=make_test_auth_headers("reaper-user"),
        )
        run_after = get_res.json()
        assert run_after["status"] == "failed"
        assert "timed out after exceeding duration" in run_after["error_message"]


# =========================================================================
# 4. Run Comparison Engine Tests
# =========================================================================


async def _seed_completed_run(
    session: AsyncSession,
    dataset: Dataset,
    owner_user_id: uuid.UUID,
    cases: list[DatasetCase],
    case_results: list[tuple[uuid.UUID, float, bool, dict[str, float]]],
    overall_score: float,
    metrics_summary: dict[str, Any] | None = None,
) -> EvaluationRun:
    """Helper to seed a completed EvaluationRun with specific case results."""
    eval_record = Evaluation(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        name=f"Eval-{uuid.uuid4().hex[:6]}",
        model_provider="openai",
        model_name="gpt-4o",
        created_at=datetime.now(UTC),
    )
    session.add(eval_record)
    await session.flush()

    run = EvaluationRun(
        id=uuid.uuid4(),
        evaluation_id=eval_record.id,
        dataset_version=dataset.version,
        dataset_snapshot_hash="snapshot-hash-test",
        status=RunStatus.COMPLETED.value,
        started_at=datetime.now(UTC) - timedelta(seconds=30),
        completed_at=datetime.now(UTC),
        duration_ms=30000.0,
        correlation_id=f"cid-{uuid.uuid4().hex[:8]}",
        overall_score=overall_score,
        total_cases=len(case_results),
        completed_cases=len(case_results),
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
                "reasoning": "Test reasoning",
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
            execution_time_ms=120.0,
            created_at=datetime.now(UTC),
        )
        session.add(res)

    await session.commit()
    await session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_compare_identical_runs() -> None:
    """Comparing identical runs produces zero delta and unchanged cases."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id="compare-user") as client:
        # Create dataset and cases
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Comparison DS"},
            headers=make_test_auth_headers("compare-user"),
        )
        ds_id = uuid.UUID(ds_res.json()["id"])
        user_id = uuid.UUID(ds_res.json()["owner_user_id"])

        c1_res = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Case 1", "expected_output": "Out 1"},
            headers=make_test_auth_headers("compare-user"),
        )
        c1_id = uuid.UUID(c1_res.json()["id"])

        c2_res = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Case 2", "expected_output": "Out 2"},
            headers=make_test_auth_headers("compare-user"),
        )
        c2_id = uuid.UUID(c2_res.json()["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, ds_id)
            assert dataset is not None

            # Seed base run
            case_results = [
                (c1_id, 0.85, True, {"factuality": 0.85}),
                (c2_id, 0.90, True, {"factuality": 0.90}),
            ]
            summary_metrics = {"metric_averages": {"factuality": 0.875}}
            base_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [],
                case_results,
                0.875,
                summary_metrics,
            )
            # Seed target run with identical scores
            target_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [],
                case_results,
                0.875,
                summary_metrics,
            )

        # Call compare API
        cmp_res = await client.get(
            f"/api/v1/evaluations/runs/compare?base_run_id={base_run.id}&target_run_id={target_run.id}",
            headers=make_test_auth_headers("compare-user"),
        )
        assert cmp_res.status_code == 200
        cmp_data = cmp_res.json()

        summary = cmp_data["summary"]
        assert summary["base_run_id"] == str(base_run.id)
        assert summary["target_run_id"] == str(target_run.id)
        assert summary["overall_score_delta"] == 0.0
        assert summary["regressions_count"] == 0
        assert summary["improvements_count"] == 0
        assert summary["unchanged_count"] == 2
        assert summary["total_cases_compared"] == 2

        # Check metric comparisons
        m_comp = summary["metric_comparisons"]["factuality"]
        assert m_comp["delta"] == 0.0
        assert m_comp["improved"] is False
        assert m_comp["regressed"] is False

        # All cases are unchanged
        for cc in cmp_data["case_comparisons"]:
            assert cc["category"] == "unchanged"
            assert cc["delta"] == 0.0

    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_runs_with_regressions_and_improvements() -> None:
    """Comparing runs flags regressions and improvements based on threshold."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id="regression-user") as client:
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Delta DS"},
            headers=make_test_auth_headers("regression-user"),
        )
        ds_id = uuid.UUID(ds_res.json()["id"])
        user_id = uuid.UUID(ds_res.json()["owner_user_id"])

        c1 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "C1"},
                headers=make_test_auth_headers("regression-user"),
            )
        ).json()
        c2 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "C2"},
                headers=make_test_auth_headers("regression-user"),
            )
        ).json()
        c3 = (
            await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={"input": "C3"},
                headers=make_test_auth_headers("regression-user"),
            )
        ).json()

        c1_id = uuid.UUID(c1["id"])
        c2_id = uuid.UUID(c2["id"])
        c3_id = uuid.UUID(c3["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, ds_id)
            assert dataset is not None

            # Base run:
            # C1: 0.90 (passed)
            # C2: 0.40 (failed)
            # C3: 0.70 (passed)
            base_results = [
                (c1_id, 0.90, True, {"relevance": 0.90}),
                (c2_id, 0.40, False, {"relevance": 0.40}),
                (c3_id, 0.70, True, {"relevance": 0.70}),
            ]
            base_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [],
                base_results,
                0.6667,
                {"metric_averages": {"relevance": 0.6667}},
            )

            # Target run:
            # C1: 0.50 (failed) -> REGRESSION
            # C2: 0.85 (passed) -> IMPROVEMENT
            # C3: 0.72 (passed) -> UNCHANGED (delta = +0.02, below threshold of 0.05)
            target_results = [
                (c1_id, 0.50, False, {"relevance": 0.50}),
                (c2_id, 0.85, True, {"relevance": 0.85}),
                (c3_id, 0.72, True, {"relevance": 0.72}),
            ]
            target_run = await _seed_completed_run(
                db_session,
                dataset,
                user_id,
                [],
                target_results,
                0.6900,
                {"metric_averages": {"relevance": 0.6900}},
            )

        # Compare with threshold=0.05
        cmp_res = await client.get(
            f"/api/v1/evaluations/runs/compare?base_run_id={base_run.id}&target_run_id={target_run.id}&threshold=0.05",
            headers=make_test_auth_headers("regression-user"),
        )
        assert cmp_res.status_code == 200
        cmp_data = cmp_res.json()

        summary = cmp_data["summary"]
        assert summary["regressions_count"] == 1
        assert summary["improvements_count"] == 1
        assert summary["unchanged_count"] == 1

        case_map = {c["case_id"]: c for c in cmp_data["case_comparisons"]}
        assert case_map[str(c1_id)]["category"] == "regression"
        assert case_map[str(c1_id)]["delta"] == -0.4
        assert case_map[str(c2_id)]["category"] == "improvement"
        assert case_map[str(c2_id)]["delta"] == 0.45
        assert case_map[str(c3_id)]["category"] == "unchanged"
        assert case_map[str(c3_id)]["delta"] == 0.02

    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_runs_cross_tenant_isolation_returns_404() -> None:
    """Comparing runs across tenants returns 404 (IDOR prevention)."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id="tenant-1") as client:
        # User 1 creates a dataset and run
        ds1 = (
            await client.post(
                "/api/v1/datasets",
                json={"name": "T1 DS"},
                headers=make_test_auth_headers("tenant-1"),
            )
        ).json()
        u1_id = uuid.UUID(ds1["owner_user_id"])
        d1_id = uuid.UUID(ds1["id"])

        # User 2 creates a dataset and run
        ds2 = (
            await client.post(
                "/api/v1/datasets",
                json={"name": "T2 DS"},
                headers=make_test_auth_headers("tenant-2"),
            )
        ).json()
        u2_id = uuid.UUID(ds2["owner_user_id"])
        d2_id = uuid.UUID(ds2["id"])

        async with session_factory() as db_session:
            dataset1 = await db_session.get(Dataset, d1_id)
            dataset2 = await db_session.get(Dataset, d2_id)
            assert dataset1 is not None and dataset2 is not None

            run1 = await _seed_completed_run(db_session, dataset1, u1_id, [], [], 0.8)
            run2 = await _seed_completed_run(db_session, dataset2, u2_id, [], [], 0.8)

        # Tenant 1 attempts to compare run1 (owned) against run2 (owned by Tenant 2)
        res1 = await client.get(
            f"/api/v1/evaluations/runs/compare?base_run_id={run1.id}&target_run_id={run2.id}",
            headers=make_test_auth_headers("tenant-1"),
        )
        assert res1.status_code == 404
        assert "not found" in res1.json()["detail"].lower()

        # Tenant 2 attempts to compare run1 (owned by Tenant 1) against run2 (owned)
        res2 = await client.get(
            f"/api/v1/evaluations/runs/compare?base_run_id={run1.id}&target_run_id={run2.id}",
            headers=make_test_auth_headers("tenant-2"),
        )
        assert res2.status_code == 404
        assert "not found" in res2.json()["detail"].lower()

    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_runs_non_terminal_returns_409() -> None:
    """Comparing non-completed runs (e.g. PENDING or RUNNING) returns 409 Conflict."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id="state-user") as client:
        ds = (
            await client.post(
                "/api/v1/datasets",
                json={"name": "State DS"},
                headers=make_test_auth_headers("state-user"),
            )
        ).json()
        u_id = uuid.UUID(ds["owner_user_id"])
        d_id = uuid.UUID(ds["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, d_id)
            assert dataset is not None

            run_completed = await _seed_completed_run(
                db_session, dataset, u_id, [], [], 0.8
            )

            # Create a pending run
            eval_record = Evaluation(
                id=uuid.uuid4(),
                dataset_id=dataset.id,
                name="Pending Eval",
                model_provider="openai",
                model_name="gpt-4o",
                created_at=datetime.now(UTC),
            )
            db_session.add(eval_record)
            await db_session.flush()

            run_pending = EvaluationRun(
                id=uuid.uuid4(),
                evaluation_id=eval_record.id,
                dataset_version=1,
                dataset_snapshot_hash="hash",
                status=RunStatus.PENDING.value,
                total_cases=1,
                completed_cases=0,
                failed_cases=0,
                created_at=datetime.now(UTC),
            )
            db_session.add(run_pending)
            await db_session.commit()

        # Comparing completed with pending -> 409
        cmp_res = await client.get(
            f"/api/v1/evaluations/runs/compare?base_run_id={run_completed.id}&target_run_id={run_pending.id}",
            headers=make_test_auth_headers("state-user"),
        )
        assert cmp_res.status_code == 409
        detail = cmp_res.json()["detail"].lower()
        assert "in progress" in detail or "completed" in detail

    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_runs_different_case_sets() -> None:
    """Comparing runs with added/removed cases classifies them appropriately."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_auth_client(default_user_id="set-user") as client:
        ds = (
            await client.post(
                "/api/v1/datasets",
                json={"name": "Dynamic Sets DS"},
                headers=make_test_auth_headers("set-user"),
            )
        ).json()
        u_id = uuid.UUID(ds["owner_user_id"])
        d_id = uuid.UUID(ds["id"])

        c1 = (
            await client.post(
                f"/api/v1/datasets/{d_id}/cases",
                json={"input": "C1"},
                headers=make_test_auth_headers("set-user"),
            )
        ).json()
        c2 = (
            await client.post(
                f"/api/v1/datasets/{d_id}/cases",
                json={"input": "C2"},
                headers=make_test_auth_headers("set-user"),
            )
        ).json()
        c3 = (
            await client.post(
                f"/api/v1/datasets/{d_id}/cases",
                json={"input": "C3"},
                headers=make_test_auth_headers("set-user"),
            )
        ).json()

        c1_id = uuid.UUID(c1["id"])
        c2_id = uuid.UUID(c2["id"])
        c3_id = uuid.UUID(c3["id"])

        async with session_factory() as db_session:
            dataset = await db_session.get(Dataset, d_id)
            assert dataset is not None

            # Base evaluates C1 and C2
            base_results = [
                (c1_id, 0.8, True, {"relevance": 0.8}),
                (c2_id, 0.8, True, {"relevance": 0.8}),
            ]
            base_run = await _seed_completed_run(
                db_session, dataset, u_id, [], base_results, 0.8
            )

            # Target evaluates C2 and C3 (C1 removed, C3 added)
            target_results = [
                (c2_id, 0.8, True, {"relevance": 0.8}),
                (c3_id, 0.9, True, {"relevance": 0.9}),
            ]
            target_run = await _seed_completed_run(
                db_session, dataset, u_id, [], target_results, 0.85
            )

        cmp_res = await client.get(
            f"/api/v1/evaluations/runs/compare?base_run_id={base_run.id}&target_run_id={target_run.id}",
            headers=make_test_auth_headers("set-user"),
        )
        assert cmp_res.status_code == 200
        cmp_data = cmp_res.json()

        summary = cmp_data["summary"]
        assert summary["total_cases_compared"] == 3
        assert summary["added_cases_count"] == 1
        assert summary["removed_cases_count"] == 1
        assert summary["unchanged_count"] == 1

        case_map = {c["case_id"]: c for c in cmp_data["case_comparisons"]}
        assert case_map[str(c1_id)]["category"] == "removed"
        assert case_map[str(c3_id)]["category"] == "added"
        assert case_map[str(c2_id)]["category"] == "unchanged"

    await engine.dispose()
