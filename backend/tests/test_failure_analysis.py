import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_auth_provider
from app.auth.provisioning import get_or_create_user
from app.auth.test_provider import TestAuthProvider, make_test_auth_headers
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.evaluation.lifecycle import RunStatus
from app.evaluation.snapshot import compute_analysis_snapshot
from app.main import app
from app.models.dataset import Dataset, DatasetCase
from app.models.evaluation import Evaluation
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.models.run_analysis import RunAnalysis
from app.schemas.failure_analysis import (
    FailureCategory,
)
from app.services.failure_analysis_service import (
    analyze_run_failures,
    classify_case_failures,
)
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_analysis_client(
    default_user_id: str | None = "analysis-user-1",
) -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient for testing failure analysis and insights."""
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
                    "experiments, run_analyses CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


async def _seed_run(
    session: AsyncSession,
    dataset: Dataset,
    user_id: uuid.UUID,
    cases_data: list[dict[str, Any]],
    run_status: str = RunStatus.COMPLETED.value,
    overall_score: float = 0.8,
) -> EvaluationRun:
    """Helper to seed an evaluation run with specific EvaluationResults."""
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
        dataset_snapshot_hash="test-snapshot-hash",
        status=run_status,
        started_at=datetime.now(UTC) - timedelta(seconds=30),
        completed_at=(
            datetime.now(UTC) if run_status == RunStatus.COMPLETED.value else None
        ),
        duration_ms=25000.0,
        correlation_id=f"cid-{uuid.uuid4().hex[:8]}",
        overall_score=overall_score,
        total_cases=len(cases_data),
        completed_cases=(
            len(cases_data) if run_status == RunStatus.COMPLETED.value else 0
        ),
        failed_cases=sum(1 for c in cases_data if not c.get("passed", True)),
        metrics_summary={},
        created_at=datetime.now(UTC) - timedelta(seconds=35),
    )
    session.add(run)
    await session.flush()

    for c in cases_data:
        case_id = c["case_id"]
        existing_case = await session.get(DatasetCase, case_id)
        if existing_case is None:
            new_case = DatasetCase(
                id=case_id,
                dataset_id=dataset.id,
                input=c.get("prompt", "Test prompt"),
            )
            session.add(new_case)
            await session.flush()

        res = EvaluationResult(
            id=uuid.uuid4(),
            run_id=run.id,
            case_id=case_id,
            response=c.get("response", "Test response"),
            overall_score=c.get("overall_score", 0.8),
            passed=c.get("passed", True),
            error_message=c.get("error_message"),
            metrics=c.get("metrics", {}),
            execution_time_ms=120.0,
            created_at=datetime.now(UTC),
        )
        session.add(res)

    await session.commit()
    await session.refresh(run)
    return run


# =========================================================================
# 1. Failure Taxonomy & Snapshot Unit Tests
# =========================================================================


def test_failure_taxonomy_enum_values() -> None:
    """All 12 required failure categories are defined."""
    expected_categories = {
        "metric_threshold_failure",
        "evaluator_error",
        "evaluator_unavailable",
        "missing_output",
        "malformed_output",
        "instruction_failure",
        "factuality_failure",
        "relevance_failure",
        "faithfulness_failure",
        "hallucination_failure",
        "consistency_failure",
        "regression_failure",
    }
    actual_categories = {c.value for c in FailureCategory}
    assert expected_categories.issubset(actual_categories)
    assert len(actual_categories) == 12


def test_compute_analysis_snapshot_determinism() -> None:
    """Snapshot calculation is deterministic irrespective of dict ordering."""
    run_id = uuid.uuid4()
    base_id = uuid.uuid4()

    data_1: dict[str, Any] = {
        "summary": "Sample summary",
        "metrics": {"factuality": 0.85, "relevance": 0.9},
        "rates": {"pass_rate": 0.95, "failure_rate": 0.05},
    }
    data_2: dict[str, Any] = {
        "rates": {"failure_rate": 0.05, "pass_rate": 0.95},
        "metrics": {"relevance": 0.9, "factuality": 0.85},
        "summary": "Sample summary",
    }

    snap_1, hash_1 = compute_analysis_snapshot(run_id, base_id, data_1)
    snap_2, hash_2 = compute_analysis_snapshot(run_id, base_id, data_2)

    assert hash_1 == hash_2
    assert len(hash_1) == 64
    assert snap_1["analysis"] == snap_2["analysis"]

    # Different data alters hash
    data_3 = {**data_1, "summary": "Altered summary"}
    _, hash_3 = compute_analysis_snapshot(run_id, base_id, data_3)
    assert hash_1 != hash_3


# =========================================================================
# 2. Case Classification Unit Tests
# =========================================================================


def test_classify_case_failures_passing() -> None:
    """A passing case with high scores produces no failure categories."""
    case_id = uuid.uuid4()
    result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        response="A valid answer.",
        overall_score=0.95,
        passed=True,
        metrics={
            "factuality": {
                "metric_name": "factuality",
                "score": 0.95,
                "threshold": 0.8,
                "status": "success",
                "passed": True,
            }
        },
    )

    analysis = classify_case_failures(result)
    assert analysis.case_id == case_id
    assert analysis.passed is True
    assert analysis.overall_case_score == 0.95
    assert len(analysis.failure_categories) == 0
    assert len(analysis.failed_metrics) == 0
    assert len(analysis.threshold_failures) == 0


def test_classify_case_failures_missing_and_malformed_output() -> None:
    """Blank output and JSON decode errors are classified into respective categories."""
    case_id = uuid.uuid4()
    blank_result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        response="   ",
        overall_score=0.0,
        passed=False,
    )
    analysis_blank = classify_case_failures(blank_result)
    assert FailureCategory.MISSING_OUTPUT in analysis_blank.failure_categories

    malformed_result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        response="{invalid json content",
        error_message=(
            "JSONDecodeError: Expecting property name enclosed in double quotes"
        ),
        overall_score=0.0,
        passed=False,
    )
    analysis_malformed = classify_case_failures(malformed_result)
    assert FailureCategory.MALFORMED_OUTPUT in analysis_malformed.failure_categories


def test_classify_case_failures_evaluator_error_and_unavailable() -> None:
    """Evaluator error and unavailable statuses are appropriately categorized."""
    case_id = uuid.uuid4()
    result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        response="Some text",
        overall_score=0.2,
        passed=False,
        metrics={
            "judge_error_metric": {
                "metric_name": "judge_error_metric",
                "score": None,
                "status": "error",
                "passed": False,
            },
            "judge_unavailable_metric": {
                "metric_name": "judge_unavailable_metric",
                "score": None,
                "status": "unavailable",
                "passed": False,
            },
        },
    )
    analysis = classify_case_failures(result)
    assert FailureCategory.EVALUATOR_ERROR in analysis.failure_categories
    assert FailureCategory.EVALUATOR_UNAVAILABLE in analysis.failure_categories
    assert "judge_error_metric" in analysis.failed_metrics
    assert "judge_unavailable_metric" in analysis.failed_metrics


def test_classify_case_failures_specific_metric_thresholds() -> None:
    """Specific metric threshold failures map to corresponding FailureCategory."""
    case_id = uuid.uuid4()
    metrics = {
        "factuality": {
            "metric_name": "factuality",
            "evaluator_type": "factuality",
            "score": 0.4,
            "threshold": 0.7,
            "status": "threshold_failed",
            "passed": False,
        },
        "faithfulness": {
            "metric_name": "faithfulness",
            "evaluator_type": "faithfulness",
            "score": 0.3,
            "threshold": 0.8,
            "status": "threshold_failed",
            "passed": False,
        },
        "consistency": {
            "metric_name": "consistency",
            "evaluator_type": "consistency",
            "score": 0.5,
            "threshold": 0.8,
            "status": "threshold_failed",
            "passed": False,
        },
    }
    result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        response="Generated text",
        overall_score=0.4,
        passed=False,
        metrics=metrics,
    )
    analysis = classify_case_failures(result)
    assert FailureCategory.FACTUALITY_FAILURE in analysis.failure_categories
    assert FailureCategory.FAITHFULNESS_FAILURE in analysis.failure_categories
    assert FailureCategory.CONSISTENCY_FAILURE in analysis.failure_categories
    assert len(analysis.threshold_failures) == 3


def test_classify_case_failures_regression_against_baseline() -> None:
    """Detects regression when a previously passing case fails in target run."""
    case_id = uuid.uuid4()
    baseline_result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        overall_score=0.9,
        passed=True,
    )
    target_result = EvaluationResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        case_id=case_id,
        overall_score=0.4,
        passed=False,
    )
    analysis = classify_case_failures(target_result, baseline_result=baseline_result)
    assert FailureCategory.REGRESSION_FAILURE in analysis.failure_categories
    assert analysis.comparison_delta == -0.5


# =========================================================================
# 3. Run Analysis Service & Aggregations Integration Tests
# =========================================================================


@pytest.mark.asyncio
async def test_analyze_run_failures_empty_run() -> None:
    """Service safely handles runs with 0 test cases without division by zero."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        user = await get_or_create_user(session, external_auth_id="empty-run-user")
        dataset = Dataset(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Empty-DS",
            created_at=datetime.now(UTC),
        )
        session.add(dataset)
        await session.flush()

        run = await _seed_run(session, dataset, user.id, cases_data=[])

        analysis = await analyze_run_failures(
            session=session,
            run_id=run.id,
            owner_user_id=user.id,
        )

        assert analysis.run_id == run.id
        assert analysis.run_insights.total_cases == 0
        assert analysis.run_insights.pass_rate == 0.0
        assert analysis.run_insights.failure_rate == 0.0
        assert len(analysis.failed_cases) == 0
        assert len(analysis.metric_distributions) == 0
        assert analysis.baseline_insights is None

        # Clean up
        await session.execute(text("TRUNCATE TABLE users CASCADE"))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_analyze_run_failures_with_baseline() -> None:
    """Test pass/fail rates, distributions, regression, and snapshot persistence."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    c1 = uuid.uuid4()
    c2 = uuid.uuid4()
    c3 = uuid.uuid4()

    async with session_factory() as session:
        user = await get_or_create_user(session, external_auth_id="baseline-user")
        dataset = Dataset(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Regression-DS",
            created_at=datetime.now(UTC),
        )
        session.add(dataset)
        await session.flush()

        # Baseline run: c1 passed, c2 passed, c3 failed
        baseline_cases = [
            {
                "case_id": c1,
                "overall_score": 0.9,
                "passed": True,
                "metrics": {
                    "factuality": {
                        "metric_name": "factuality",
                        "score": 0.9,
                        "threshold": 0.7,
                        "status": "success",
                        "passed": True,
                    }
                },
            },
            {
                "case_id": c2,
                "overall_score": 0.85,
                "passed": True,
                "metrics": {
                    "factuality": {
                        "metric_name": "factuality",
                        "score": 0.85,
                        "threshold": 0.7,
                        "status": "success",
                        "passed": True,
                    }
                },
            },
            {
                "case_id": c3,
                "overall_score": 0.4,
                "passed": False,
                "metrics": {
                    "factuality": {
                        "metric_name": "factuality",
                        "score": 0.4,
                        "threshold": 0.7,
                        "status": "threshold_failed",
                        "passed": False,
                    }
                },
            },
        ]
        base_run = await _seed_run(
            session, dataset, user.id, baseline_cases, overall_score=0.717
        )

        # Target run: c1 passed, c2 failed (regressed), c3 passed (recovered)
        target_cases = [
            {
                "case_id": c1,
                "overall_score": 0.95,
                "passed": True,
                "metrics": {
                    "factuality": {
                        "metric_name": "factuality",
                        "score": 0.95,
                        "threshold": 0.7,
                        "status": "success",
                        "passed": True,
                    }
                },
            },
            {
                "case_id": c2,
                "overall_score": 0.45,
                "passed": False,
                "metrics": {
                    "factuality": {
                        "metric_name": "factuality",
                        "score": 0.45,
                        "threshold": 0.7,
                        "status": "threshold_failed",
                        "passed": False,
                    }
                },
            },
            {
                "case_id": c3,
                "overall_score": 0.8,
                "passed": True,
                "metrics": {
                    "factuality": {
                        "metric_name": "factuality",
                        "score": 0.8,
                        "threshold": 0.7,
                        "status": "success",
                        "passed": True,
                    }
                },
            },
        ]
        target_run = await _seed_run(
            session, dataset, user.id, target_cases, overall_score=0.733
        )

        analysis = await analyze_run_failures(
            session=session,
            run_id=target_run.id,
            owner_user_id=user.id,
            baseline_run_id=base_run.id,
        )

        # 1. Run Insights
        assert analysis.run_insights.total_cases == 3
        assert analysis.run_insights.passed_cases == 2
        assert analysis.run_insights.failed_cases == 1
        assert analysis.run_insights.pass_rate == 0.6667
        assert analysis.run_insights.failure_rate == 0.3333

        # 2. Metric Distributions
        assert len(analysis.metric_distributions) == 1
        fact_dist = analysis.metric_distributions[0]
        assert fact_dist.metric_name == "factuality"
        assert fact_dist.successful_evaluations == 2
        assert fact_dist.threshold_failed == 1
        assert fact_dist.failure_percentage == 33.33

        # 3. Failed Cases
        assert len(analysis.failed_cases) == 1
        failed_c = analysis.failed_cases[0]
        assert failed_c.case_id == c2
        assert FailureCategory.REGRESSION_FAILURE in failed_c.failure_categories
        assert FailureCategory.FACTUALITY_FAILURE in failed_c.failure_categories

        # 4. Baseline Insights
        assert analysis.baseline_insights is not None
        b_ins = analysis.baseline_insights
        assert b_ins.baseline_run_id == base_run.id
        assert b_ins.newly_failing_cases == [c2]
        assert b_ins.newly_passing_cases == [c3]
        assert b_ins.unchanged_passes == [c1]
        assert b_ins.unchanged_failures == []
        assert b_ins.regression_rate == 0.3333  # 1 out of 3 total cases regressed
        assert b_ins.recovery_rate == 0.3333  # 1 out of 3 total cases recovered

        # 5. Persistence verification
        persisted_stmt = select(RunAnalysis).where(RunAnalysis.run_id == target_run.id)
        persisted = (await session.scalars(persisted_stmt)).first()
        assert persisted is not None
        assert persisted.snapshot_hash == analysis.snapshot_hash
        assert persisted.baseline_run_id == base_run.id

        # Clean up
        await session.execute(text("TRUNCATE TABLE users CASCADE"))
        await session.commit()
    await engine.dispose()


# =========================================================================
# 4. API Router Integration Tests
# =========================================================================


@pytest.mark.asyncio
async def test_api_get_run_analysis_success() -> None:
    """API endpoint returns 200 with full analysis response."""
    async with managed_analysis_client(default_user_id="user-analysis-ok") as client:
        auth_headers = make_test_auth_headers("user-analysis-ok")

        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        c1 = uuid.uuid4()
        async with session_factory() as session:
            user = await get_or_create_user(
                session, external_auth_id="user-analysis-ok"
            )
            ds = Dataset(
                id=uuid.uuid4(),
                owner_user_id=user.id,
                name="API-Analysis-DS",
                created_at=datetime.now(UTC),
            )
            session.add(ds)
            await session.flush()

            target_run = await _seed_run(
                session,
                ds,
                user.id,
                cases_data=[
                    {
                        "case_id": c1,
                        "overall_score": 0.3,
                        "passed": False,
                        "response": "Answer",
                        "metrics": {
                            "relevance": {
                                "metric_name": "relevance",
                                "score": 0.3,
                                "threshold": 0.8,
                                "status": "threshold_failed",
                                "passed": False,
                            }
                        },
                    }
                ],
            )
        await engine.dispose()

        res = await client.get(
            f"/api/v1/evaluations/runs/{target_run.id}/analysis",
            headers={**auth_headers, "X-Correlation-ID": "test-cid-1234"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["run_id"] == str(target_run.id)
        assert data["run_insights"]["total_cases"] == 1
        assert data["run_insights"]["failed_cases"] == 1
        assert len(data["failed_cases"]) == 1
        assert data["failed_cases"][0]["case_id"] == str(c1)
        assert res.headers["X-Correlation-ID"] == "test-cid-1234"


@pytest.mark.asyncio
async def test_api_get_run_analysis_tenancy_isolation() -> None:
    """User B cannot access or analyze User A's evaluation runs (returns 404)."""
    async with managed_analysis_client(default_user_id="user-a") as client:
        auth_headers_b = make_test_auth_headers("user-b")

        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            user_a = await get_or_create_user(session, external_auth_id="user-a")
            await get_or_create_user(session, external_auth_id="user-b")
            ds = Dataset(
                id=uuid.uuid4(),
                owner_user_id=user_a.id,
                name="Tenant-A-DS",
                created_at=datetime.now(UTC),
            )
            session.add(ds)
            await session.flush()

            run_a = await _seed_run(session, ds, user_a.id, cases_data=[])
        await engine.dispose()

        # User B attempts to access User A's run analysis
        res_b = await client.get(
            f"/api/v1/evaluations/runs/{run_a.id}/analysis",
            headers=auth_headers_b,
        )
        assert res_b.status_code == 404


@pytest.mark.asyncio
async def test_api_get_run_analysis_non_terminal_conflict() -> None:
    """Non-terminal (PENDING or RUNNING) target run returns 409 Conflict."""
    async with managed_analysis_client(default_user_id="user-conflict") as client:
        auth_headers = make_test_auth_headers("user-conflict")

        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            user = await get_or_create_user(session, external_auth_id="user-conflict")
            ds = Dataset(
                id=uuid.uuid4(),
                owner_user_id=user.id,
                name="Conflict-DS",
                created_at=datetime.now(UTC),
            )
            session.add(ds)
            await session.flush()

            running_run = await _seed_run(
                session,
                ds,
                user.id,
                cases_data=[],
                run_status=RunStatus.RUNNING.value,
            )
        await engine.dispose()

        res = await client.get(
            f"/api/v1/evaluations/runs/{running_run.id}/analysis",
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert "non-terminal" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_get_run_analysis_unauthenticated() -> None:
    """Requests without authentication return 401 Unauthorized."""
    async with managed_analysis_client(default_user_id=None) as client:
        random_id = uuid.uuid4()
        res = await client.get(f"/api/v1/evaluations/runs/{random_id}/analysis")
        assert res.status_code == 401
