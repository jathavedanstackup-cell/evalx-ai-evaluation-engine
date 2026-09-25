import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.errors import InvalidRunStateTransitionError
from app.evaluation.lifecycle import RunStatus, validate_run_transition
from app.evaluation.snapshot import compute_dataset_snapshot_hash
from app.evaluation.types import EvaluationCaseResult, EvaluationMetric
from app.main import app
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.services.evaluation_run_service import _determine_case_status
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_api_client() -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient connected exclusively to the TEST database."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    from app.auth.dependencies import get_auth_provider
    from app.auth.test_provider import TestAuthProvider

    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id="test-user-default"
    )
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
# 1. Dataset Reproducibility & Snapshot Hashing Unit Tests
# =========================================================================


def test_snapshot_hash_identical_datasets() -> None:
    case_1 = {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "input": "Prompt 1",
        "context": ["Passage A"],
        "expected_output": "Answer 1",
        "metadata": {"category": "math"},
    }
    case_2 = {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "input": "Prompt 2",
        "context": ["Passage B"],
        "expected_output": "Answer 2",
        "metadata": {"category": "science"},
    }

    hash_a = compute_dataset_snapshot_hash([case_1, case_2])
    hash_b = compute_dataset_snapshot_hash([case_1, case_2])

    assert hash_a == hash_b
    assert len(hash_a) == 64


def test_snapshot_hash_order_independence() -> None:
    case_1 = {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "input": "Prompt 1",
        "context": ["Passage A"],
        "expected_output": "Answer 1",
        "metadata": None,
    }
    case_2 = {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "input": "Prompt 2",
        "context": ["Passage B"],
        "expected_output": "Answer 2",
        "metadata": None,
    }

    # Regardless of input list order, canonical sort by case_id produces identical hash
    hash_forward = compute_dataset_snapshot_hash([case_1, case_2])
    hash_reversed = compute_dataset_snapshot_hash([case_2, case_1])

    assert hash_forward == hash_reversed


def test_snapshot_hash_changed_content() -> None:
    case_base = {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "input": "Prompt 1",
        "context": ["Passage A"],
        "expected_output": "Answer 1",
        "metadata": {"tag": "v1"},
    }
    base_hash = compute_dataset_snapshot_hash([case_base])

    # Changed input
    c_input = dict(case_base, input="Prompt 1 modified")
    assert compute_dataset_snapshot_hash([c_input]) != base_hash

    # Changed expected output
    c_expected = dict(case_base, expected_output="Different answer")
    assert compute_dataset_snapshot_hash([c_expected]) != base_hash

    # Changed context
    c_context = dict(case_base, context=["Different passage"])
    assert compute_dataset_snapshot_hash([c_context]) != base_hash

    # Changed metadata
    c_meta = dict(case_base, metadata={"tag": "v2"})
    assert compute_dataset_snapshot_hash([c_meta]) != base_hash


# =========================================================================
# 2. Lifecycle State Machine Tests
# =========================================================================


def test_lifecycle_legal_transitions() -> None:
    assert (
        validate_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
        == RunStatus.RUNNING
    )
    assert (
        validate_run_transition(RunStatus.RUNNING, RunStatus.COMPLETED)
        == RunStatus.COMPLETED
    )
    assert (
        validate_run_transition(RunStatus.PENDING, RunStatus.FAILED) == RunStatus.FAILED
    )
    assert (
        validate_run_transition(RunStatus.RUNNING, RunStatus.FAILED) == RunStatus.FAILED
    )


def test_lifecycle_illegal_transitions() -> None:
    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.COMPLETED, RunStatus.RUNNING)

    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.FAILED, RunStatus.COMPLETED)

    with pytest.raises(InvalidRunStateTransitionError):
        validate_run_transition(RunStatus.PENDING, RunStatus.COMPLETED)


# =========================================================================
# 3. Case-Level Status & Aggregation Unit Tests
# =========================================================================


def test_determine_case_status_error() -> None:
    case_res = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        response="Test",
        metrics={
            "factuality": EvaluationMetric(
                metric_name="factuality",
                evaluator_type=EvaluatorType.FACTUALITY,
                status=MetricStatus.ERROR,
            ),
            "relevance": EvaluationMetric(
                metric_name="relevance",
                evaluator_type=EvaluatorType.RELEVANCE,
                score=0.9,
                passed=True,
                status=MetricStatus.SUCCESS,
            ),
        },
    )
    assert _determine_case_status(case_res) == "error"


def test_determine_case_status_threshold_failed() -> None:
    case_res = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        response="Test",
        passed=False,
        metrics={
            "factuality": EvaluationMetric(
                metric_name="factuality",
                evaluator_type=EvaluatorType.FACTUALITY,
                score=0.4,
                passed=False,
                status=MetricStatus.THRESHOLD_FAILED,
            ),
            "relevance": EvaluationMetric(
                metric_name="relevance",
                evaluator_type=EvaluatorType.RELEVANCE,
                score=0.9,
                passed=True,
                status=MetricStatus.SUCCESS,
            ),
        },
    )
    assert _determine_case_status(case_res) == "threshold_failed"


def test_determine_case_status_unavailable() -> None:
    case_res = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        response="Test",
        metrics={
            "consistency": EvaluationMetric(
                metric_name="consistency",
                evaluator_type=EvaluatorType.CONSISTENCY,
                status=MetricStatus.UNAVAILABLE,
            )
        },
    )
    assert _determine_case_status(case_res) == "unavailable"


def test_determine_case_status_skipped() -> None:
    case_res = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        response="Test",
        metrics={
            "exact_match": EvaluationMetric(
                metric_name="exact_match",
                evaluator_type=EvaluatorType.INSTRUCTION_FOLLOWING,
                status=MetricStatus.SKIPPED,
            )
        },
    )
    assert _determine_case_status(case_res) == "skipped"


def test_determine_case_status_success() -> None:
    case_res = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        response="Test",
        passed=True,
        metrics={
            "exact_match": EvaluationMetric(
                metric_name="exact_match",
                evaluator_type=EvaluatorType.INSTRUCTION_FOLLOWING,
                score=1.0,
                passed=True,
                status=MetricStatus.SUCCESS,
            )
        },
    )
    assert _determine_case_status(case_res) == "success"


# =========================================================================
# 4. API End-to-End Integration Tests
# =========================================================================


async def _create_test_dataset(
    client: AsyncClient, count: int = 2
) -> tuple[str, list[str]]:
    """Helper to create a dataset and bulk ingest test cases."""
    ds_resp = await client.post(
        "/api/v1/datasets",
        json={"name": "Eval Test Dataset", "description": "For run execution testing"},
    )
    assert ds_resp.status_code == 201
    dataset_id = ds_resp.json()["id"]

    cases_payload = {
        "cases": [
            {
                "input": f"Question {i}",
                "expected_output": f"Answer {i}",
                "context": [f"Passage {i}"],
                "metadata": {"test_idx": i},
            }
            for i in range(count)
        ]
    }
    cases_resp = await client.post(
        f"/api/v1/datasets/{dataset_id}/cases/bulk",
        json=cases_payload,
    )
    assert cases_resp.status_code == 201
    case_ids = [c["id"] for c in cases_resp.json()["cases"]]
    return dataset_id, case_ids


@pytest.mark.asyncio
async def test_create_and_execute_evaluation_run_success() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=2)

        run_payload = {
            "dataset_id": dataset_id,
            "name": "Integration Test Run",
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "threshold": 1.0,
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Answer 0"},  # exact match
                {"case_id": case_ids[1], "response": "Different output"},  # fail
            ],
        }

        resp = await client.post("/api/v1/evaluations/runs", json=run_payload)
        assert resp.status_code == 201
        data = resp.json()

        assert data["status"] == "completed"
        assert data["dataset_id"] == dataset_id
        assert data["dataset_version"] == 1
        assert len(data["dataset_snapshot_hash"]) == 64
        assert data["total_cases"] == 2
        assert data["completed_cases"] == 1
        assert data["failed_cases"] == 1
        assert data["overall_score"] == 0.5
        assert data["metrics_summary"] is not None

        run_id = data["id"]

        # Verify GET /api/v1/evaluations/runs/{run_id}
        get_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == run_id

        # Verify GET /api/v1/evaluations/runs/{run_id}/results
        results_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}/results")
        assert results_resp.status_code == 200
        results_data = results_resp.json()
        assert results_data["total"] == 2
        assert len(results_data["items"]) == 2

        # Check case-level aggregate results
        res_0 = next(r for r in results_data["items"] if r["case_id"] == case_ids[0])
        assert res_0["passed"] is True
        assert res_0["status"] == "success"
        assert res_0["instruction_score"] == 1.0
        assert res_0["metrics"] is not None

        res_1 = next(r for r in results_data["items"] if r["case_id"] == case_ids[1])
        assert res_1["passed"] is False
        assert res_1["status"] == "threshold_failed"
        assert res_1["instruction_score"] == 0.0


@pytest.mark.asyncio
async def test_list_evaluation_runs() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                }
            ],
            "responses": [{"case_id": case_ids[0], "response": "Answer 0"}],
        }
        create_resp = await client.post("/api/v1/evaluations/runs", json=payload)
        assert create_resp.status_code == 201

        # List all runs
        list_resp = await client.get("/api/v1/evaluations/runs")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] >= 1
        assert data["items"][0]["status"] == "completed"

        # List by dataset filter
        filtered_resp = await client.get(
            f"/api/v1/evaluations/runs?dataset_id={dataset_id}"
        )
        assert filtered_resp.status_code == 200
        assert filtered_resp.json()["total"] == 1

        # List by status filter
        status_resp = await client.get("/api/v1/evaluations/runs?status=completed")
        assert status_resp.status_code == 200
        assert status_resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_run_with_existing_evaluation_id() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        # 1. First run creates the Evaluation
        payload_1 = {
            "dataset_id": dataset_id,
            "name": "Base Evaluation",
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                }
            ],
        }
        resp_1 = await client.post("/api/v1/evaluations/runs", json=payload_1)
        assert resp_1.status_code == 201
        eval_id = resp_1.json()["evaluation_id"]

        # 2. Second run reuses the existing evaluation_id
        payload_2 = {
            "dataset_id": dataset_id,
            "evaluation_id": eval_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                }
            ],
        }
        resp_2 = await client.post("/api/v1/evaluations/runs", json=payload_2)
        assert resp_2.status_code == 201
        assert resp_2.json()["evaluation_id"] == eval_id


@pytest.mark.asyncio
async def test_fatal_run_failure_containment() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=2)

        # Mock engine to simulate a fatal runtime failure during execution
        with patch(
            "app.evaluation.engine.EvaluationEngine.evaluate_run",
            side_effect=RuntimeError("Simulated unrecoverable orchestration crash"),
        ):
            payload = {
                "dataset_id": dataset_id,
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                    }
                ],
            }
            resp = await client.post("/api/v1/evaluations/runs", json=payload)
            assert resp.status_code == 500
            assert (
                "Simulated unrecoverable orchestration crash" in resp.json()["detail"]
            )

        # Verify that the run is marked FAILED and no orphaned RUNNING runs exist
        runs_resp = await client.get("/api/v1/evaluations/runs?status=failed")
        assert runs_resp.status_code == 200
        runs = runs_resp.json()["items"]
        assert len(runs) == 1
        failed_run = runs[0]
        assert failed_run["status"] == "failed"
        assert "Simulated unrecoverable" in failed_run["error_message"]

        # Verify ATOMIC CONTAINMENT: zero partial results were persisted
        # for this failed run!
        results_resp = await client.get(
            f"/api/v1/evaluations/runs/{failed_run['id']}/results"
        )

        assert results_resp.status_code == 200
        assert results_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_validation_empty_dataset() -> None:
    async with managed_api_client() as client:
        ds_resp = await client.post(
            "/api/v1/datasets",
            json={"name": "Empty Dataset"},
        )
        empty_id = ds_resp.json()["id"]

        resp = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": empty_id,
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                    }
                ],
            },
        )
        assert resp.status_code == 400
        assert "has no cases to evaluate" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_validation_unknown_dataset() -> None:
    async with managed_api_client() as client:
        resp = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": str(uuid.uuid4()),
                "evaluators": [
                    {
                        "evaluator_type": "instruction_following",
                        "backend": "native",
                    }
                ],
            },
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_validation_invalid_evaluator_type() -> None:
    async with managed_api_client() as client:
        dataset_id, _ = await _create_test_dataset(client, count=1)

        resp = await client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_id": dataset_id,
                "evaluators": [
                    {
                        "evaluator_type": "completely_bogus_type",
                        "backend": "llm_judge",
                    }
                ],
            },
        )
        assert resp.status_code == 400
        assert "Invalid evaluator configuration" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_validation_exceeding_evaluators_limit() -> None:
    async with managed_api_client() as client:
        dataset_id, _ = await _create_test_dataset(client, count=1)

        evals = [
            {"evaluator_type": "instruction_following", "backend": "native"}
            for _ in range(11)  # max is 10
        ]
        resp = await client.post(
            "/api/v1/evaluations/runs",
            json={"dataset_id": dataset_id, "evaluators": evals},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_result_prevention_constraint() -> None:
    """Verifies that the unique constraint on (run_id, case_id) prevents duplicates."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    run_id = uuid.uuid4()

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)
        actual_case_id = uuid.UUID(case_ids[0])

        # Create a run record in DB
        async with session_factory() as session:
            run = EvaluationRun(
                id=run_id,
                evaluation_id=uuid.uuid4(),
                status="pending",
                dataset_version=1,
                dataset_snapshot_hash="test",
            )
            # Need a valid evaluation
            from app.models.evaluation import Evaluation

            eval_rec = Evaluation(
                id=run.evaluation_id,
                name="Test",
                model_provider="p",
                model_name="m",
                dataset_id=uuid.UUID(dataset_id),
            )
            session.add(eval_rec)
            session.add(run)

            res1 = EvaluationResult(
                id=uuid.uuid4(),
                run_id=run_id,
                case_id=actual_case_id,
                response="Resp 1",
            )
            session.add(res1)
            await session.commit()

            # Attempting to insert a second result for the same (run_id, case_id)
            # MUST fail due to unique constraint uq_result_run_case
            res2 = EvaluationResult(
                id=uuid.uuid4(),
                run_id=run_id,
                case_id=actual_case_id,
                response="Duplicate Resp",
            )

            session.add(res2)
            with pytest.raises(IntegrityError):
                await session.commit()

    await engine.dispose()
