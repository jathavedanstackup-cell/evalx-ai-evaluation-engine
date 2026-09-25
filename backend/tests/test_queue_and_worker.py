import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from arq.worker import Retry
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.evaluation.enums import EvaluatorType
from app.evaluation.errors import (
    QueueUnavailableError,
    RunEnqueueError,
    UnsupportedEvaluatorError,
)
from app.evaluation.lifecycle import RunStatus
from app.infrastructure.queue import InMemoryEvaluationQueue, RedisEvaluationQueue
from app.main import app
from app.models.dataset import Dataset, DatasetCase
from app.models.evaluation_run import EvaluationRun
from app.observability import EvaluationEventType, EvaluationObservability
from app.schemas.evaluation_run import (
    MAX_TOTAL_RESPONSES_BYTES,
    CaseResponseInput,
    EvaluationRunCreate,
    EvaluatorConfigInput,
)
from app.services.run_executor import QueuedRunExecutor
from app.worker.heartbeat import check_worker_heartbeat, write_worker_heartbeat
from app.worker.tasks import execute_run_task


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


@asynccontextmanager
async def managed_test_session() -> AsyncIterator[AsyncSession]:
    """Provides direct AsyncSession connected to TEST database."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results CASCADE"
                )
            )
            await session.commit()
    await engine.dispose()


async def _create_test_dataset(
    client: AsyncClient, count: int = 2
) -> tuple[str, list[str]]:
    """Helper to create a dataset and bulk ingest test cases."""
    ds_resp = await client.post(
        "/api/v1/datasets",
        json={
            "name": "Queue Test Dataset",
            "description": "For queue and worker tests",
        },
    )
    assert ds_resp.status_code == 201
    dataset_id = ds_resp.json()["id"]

    cases_payload = {
        "cases": [
            {
                "input": f"Question {i}",
                "expected_output": f"Expected Answer {i}",
                "context": [f"Reference text {i}"],
                "metadata": {"idx": i},
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


# =============================================================================
# 1. QUEUE PROTOCOL & INFRASTRUCTURE TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_in_memory_queue_operations() -> None:
    queue = InMemoryEvaluationQueue()
    assert await queue.ping() is True
    assert len(queue.jobs) == 0

    run_id_1 = uuid.uuid4()
    job_id_1 = await queue.enqueue(run_id_1, "corr-1")
    assert job_id_1.startswith("evalx-job-")
    assert len(queue.jobs) == 1
    assert queue.jobs[0]["run_id"] == str(run_id_1)
    assert queue.jobs[0]["correlation_id"] == "corr-1"

    run_id_2 = uuid.uuid4()
    await queue.enqueue(run_id_2, "corr-2")
    assert len(queue.jobs) == 2

    dequeued = await queue.dequeue()
    assert dequeued is not None
    assert dequeued["run_id"] == str(run_id_1)
    assert dequeued["correlation_id"] == "corr-1"
    assert len(queue.jobs) == 1

    queue.clear()
    assert len(queue.jobs) == 0
    await queue.close()


@pytest.mark.asyncio
async def test_redis_queue_minimal_job_payload() -> None:
    queue = RedisEvaluationQueue()
    if not await queue.ping():
        pytest.skip("Redis not reachable for live Redis queue test")

    run_id = uuid.uuid4()
    corr_id = "corr-redis-test"
    job_id = await queue.enqueue(run_id, corr_id)
    assert job_id == f"evalx-run-{run_id}"
    await queue.close()


# =============================================================================
# 2. QUEUED RUN EXECUTOR TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_queued_run_executor_submits_pending_and_enqueues() -> None:
    async with managed_test_session() as session:
        dataset = Dataset(name="Queue Test DS", description="desc")
        session.add(dataset)
        await session.flush()
        case_0 = DatasetCase(dataset_id=dataset.id, input="Q0", expected_output="A0")
        case_1 = DatasetCase(dataset_id=dataset.id, input="Q1", expected_output="A1")
        session.add_all([case_0, case_1])
        await session.commit()

        dataset_id = dataset.id
        case_ids = [case_0.id, case_1.id]

        queue = InMemoryEvaluationQueue()
        obs = EvaluationObservability()
        events: list[Any] = []
        obs.subscribe(lambda ev: events.append(ev))

        executor = QueuedRunExecutor(queue=queue, observability=obs)

        data = EvaluationRunCreate(
            dataset_id=dataset_id,
            evaluators=[
                EvaluatorConfigInput(
                    evaluator_type=EvaluatorType.INSTRUCTION_FOLLOWING.value,
                    backend="native",
                    name="exact_match",
                    threshold=1.0,
                    configuration={"case_sensitive": False},
                )
            ],
            responses=[
                CaseResponseInput(case_id=case_ids[0], response="Expected Answer 0"),
                CaseResponseInput(case_id=case_ids[1], response="Expected Answer 1"),
            ],
            execution_metadata={"env": "test_queue"},
        )

        run = await executor.submit_and_execute(
            session=session,
            data=data,
            correlation_id="corr-queue-exec",
        )

        # 1. Run returned immediately in PENDING status
        assert run.status == RunStatus.PENDING.value
        assert run.correlation_id == "corr-queue-exec"
        assert run.execution_metadata == {"env": "test_queue"}
        assert run.total_cases == 2
        assert run.completed_cases == 0

        # 2. Database verification: persisted as PENDING
        db_run = await session.get(EvaluationRun, run.id)
        assert db_run is not None
        assert db_run.status == RunStatus.PENDING.value
        assert db_run.candidate_responses is not None
        assert len(db_run.candidate_responses) == 2

        # 3. Queue payload verification: minimal job payload ONLY
        assert len(queue.jobs) == 1
        job = queue.jobs[0]
        assert job["run_id"] == str(run.id)
        assert job["correlation_id"] == "corr-queue-exec"
        # Candidate responses and prompts must NEVER be in queue payload
        assert "responses" not in job
        assert "cases" not in job

        # 4. Observability verification: RUN_CREATED emitted
        event_types = [e.event_type for e in events]
        assert EvaluationEventType.RUN_CREATED in event_types


@pytest.mark.asyncio
async def test_queued_run_executor_queue_failure_marks_run_failed() -> None:
    class FailingQueue:
        async def enqueue(self, run_id: uuid.UUID, correlation_id: str) -> str:
            raise QueueUnavailableError("Redis connection refused")

        async def ping(self) -> bool:
            return False

        async def close(self) -> None:
            pass

    async with managed_test_session() as session:
        dataset = Dataset(name="Queue Fail DS", description="desc")
        session.add(dataset)
        await session.flush()
        case_0 = DatasetCase(dataset_id=dataset.id, input="Q0", expected_output="A0")
        session.add(case_0)
        await session.commit()

        dataset_id = dataset.id
        case_ids = [case_0.id]

        failing_queue = FailingQueue()
        obs = EvaluationObservability()
        events: list[Any] = []
        obs.subscribe(lambda ev: events.append(ev))

        executor = QueuedRunExecutor(queue=failing_queue, observability=obs)  # type: ignore[arg-type]

        data = EvaluationRunCreate(
            dataset_id=dataset_id,
            evaluators=[
                EvaluatorConfigInput(
                    evaluator_type=EvaluatorType.INSTRUCTION_FOLLOWING.value,
                    backend="native",
                    name="exact_match",
                    threshold=1.0,
                )
            ],
            responses=[
                CaseResponseInput(case_id=case_ids[0], response="Expected Answer 0"),
            ],
        )

        with pytest.raises(RunEnqueueError) as exc_info:
            await executor.submit_and_execute(
                session=session,
                data=data,
                correlation_id="corr-failing-queue",
            )

        # 1. Raised exception is domain exception, NOT FastAPI HTTPException
        assert isinstance(exc_info.value, RunEnqueueError)

        # 2. Run in DB must NOT remain PENDING; must be marked FAILED
        stmt = select(EvaluationRun).order_by(EvaluationRun.created_at.desc())
        res = await session.execute(stmt)
        failed_run = res.scalars().first()
        assert failed_run is not None
        assert failed_run.status == RunStatus.FAILED.value
        assert failed_run.duration_ms == 0.0
        assert "Failed to enqueue" in (failed_run.error_message or "")

        # 3. Observability event RUN_FAILED emitted
        failed_events = [
            e for e in events if e.event_type == EvaluationEventType.RUN_FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0].status == RunStatus.FAILED.value


# =============================================================================
# 3. API ASYNCHRONOUS CONTRACT TESTS (POST /api/v1/evaluations/runs)
# =============================================================================


@pytest.mark.asyncio
async def test_api_post_run_returns_202_accepted_with_pending_status() -> None:
    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=2)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "exact_match_eval",
                    "threshold": 1.0,
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Expected Answer 0"},
                {"case_id": case_ids[1], "response": "Expected Answer 1"},
            ],
            "execution_metadata": {"client": "test_api_202"},
        }

        resp = await client.post(
            "/api/v1/evaluations/runs",
            json=payload,
            headers={"X-Correlation-ID": "corr-client-202"},
        )

        # Must return HTTP 202 Accepted (not 201)
        assert resp.status_code == 202
        data = resp.json()

        assert data["status"] == "pending"
        assert data["correlation_id"] == "corr-client-202"
        assert resp.headers.get("x-correlation-id") == "corr-client-202"
        assert data["dataset_id"] == dataset_id
        assert data["dataset_version"] == 1
        assert len(data["dataset_snapshot_hash"]) == 64
        assert data["total_cases"] == 2
        assert data["completed_cases"] == 0
        assert data["failed_cases"] == 0
        assert data["overall_score"] is None
        # Candidate responses must NOT be exposed in run response
        assert "responses" not in data
        assert "candidate_responses" not in data

        # Job must be in queue
        assert len(queue.jobs) == 1
        assert queue.jobs[0]["run_id"] == data["id"]
        assert queue.jobs[0]["correlation_id"] == "corr-client-202"


@pytest.mark.asyncio
async def test_api_post_run_queue_unavailable_returns_503() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    class UnavailableQueue:
        async def enqueue(self, run_id: uuid.UUID, correlation_id: str) -> str:
            msg = (
                "Redis queue unavailable at "
                "redis://evalx:secret123@internal-redis.cluster.local:6379/0: "
                "ConnectionRefusedError: [Errno 111] Connection refused"
            )
            raise QueueUnavailableError(msg)

        async def ping(self) -> bool:
            return False

        async def close(self) -> None:
            pass

    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(
        queue=UnavailableQueue()  # type: ignore[arg-type]
    )

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [{"case_id": case_ids[0], "response": "Test response"}],
        }

        resp = await client.post("/api/v1/evaluations/runs", json=payload)
        assert resp.status_code == 503
        data = resp.json()

        # 1. API client receives a generic safe message
        assert data["detail"] == "Evaluation queue is currently unavailable."

        # 2. Response must NOT contain internal URLs, hostnames, ports,
        # credentials, or stack traces
        raw_resp = resp.text
        assert "redis://" not in raw_resp
        assert "internal-redis.cluster.local" not in raw_resp
        assert "localhost" not in raw_resp
        assert "redis" not in raw_resp.lower()
        assert "6379" not in raw_resp
        assert "secret123" not in raw_resp
        assert "Traceback" not in raw_resp
        assert "ConnectionRefusedError" not in raw_resp
        assert "QueueUnavailableError" not in raw_resp

        # 3. Internal sanitized diagnostic behavior remains available in DB record
        async with session_factory() as session:
            stmt = select(EvaluationRun).order_by(EvaluationRun.created_at.desc())
            res = await session.execute(stmt)
            failed_run = res.scalars().first()
            assert failed_run is not None
            assert failed_run.status == RunStatus.FAILED.value
            assert failed_run.duration_ms == 0.0
            # Technical diagnostic error is captured
            assert "Failed to enqueue evaluation run to background queue" in (
                failed_run.error_message or ""
            )
            # Sensitive credentials must remain redacted in DB diagnostics
            assert "secret123" not in (failed_run.error_message or "")
            assert "[REDACTED]" in (failed_run.error_message or "")

    await engine.dispose()


@pytest.mark.asyncio
async def test_api_candidate_responses_payload_limit_enforced() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        # Oversized response exceeding MAX_TOTAL_RESPONSES_BYTES (2MB)
        oversized_str = "x" * (MAX_TOTAL_RESPONSES_BYTES + 50)
        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [{"case_id": case_ids[0], "response": oversized_str}],
        }

        resp = await client.post("/api/v1/evaluations/runs", json=payload)
        # Bounded validation rejects payload with 422 before DB persistence
        assert resp.status_code == 422
        assert "exceeds maximum limit of 2000000 bytes" in str(resp.json())


@pytest.mark.asyncio
async def test_candidate_responses_not_exposed_in_run_listing() -> None:
    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Confidential Model Output"}
            ],
        }

        create_resp = await client.post("/api/v1/evaluations/runs", json=payload)
        assert create_resp.status_code == 202
        run_id = create_resp.json()["id"]

        # 1. GET /runs
        list_resp = await client.get("/api/v1/evaluations/runs")
        assert list_resp.status_code == 200
        run_summary = next(r for r in list_resp.json()["items"] if r["id"] == run_id)
        assert "candidate_responses" not in run_summary
        assert "responses" not in run_summary

        # 2. GET /runs/{run_id}
        get_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}")
        assert get_resp.status_code == 200
        assert "candidate_responses" not in get_resp.json()
        assert "responses" not in get_resp.json()


# =============================================================================
# 4. WORKER EXECUTION & ATOMIC CLAIMING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_worker_task_executes_pending_run_to_completion() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=2)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "exact_match",
                    "threshold": 1.0,
                    "configuration": {"case_sensitive": False},
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Expected Answer 0"},  # pass
                {"case_id": case_ids[1], "response": "Wrong answer"},  # fail
            ],
        }

        post_resp = await client.post(
            "/api/v1/evaluations/runs",
            json=payload,
            headers={"X-Correlation-ID": "corr-worker-exec"},
        )
        assert post_resp.status_code == 202
        run_id = post_resp.json()["id"]

        # Run is initially pending
        pending_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}")
        assert pending_resp.json()["status"] == "pending"

        # Worker consumes and executes the task
        ctx = {
            "session_factory": session_factory,
            "worker_id": "evalx-worker-test-1",
            "job_id": f"job-{run_id}",
            "job_try": 1,
        }
        await execute_run_task(ctx, run_id=run_id, correlation_id="corr-worker-exec")

        # Verify run reached COMPLETED
        completed_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}")
        assert completed_resp.status_code == 200
        data = completed_resp.json()
        assert data["status"] == "completed"
        assert data["total_cases"] == 2
        assert data["completed_cases"] == 1
        assert data["failed_cases"] == 1
        assert data["overall_score"] == 0.5
        assert data["duration_ms"] > 0.0

        # Verify results endpoint
        results_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}/results")
        assert results_resp.status_code == 200
        results_data = results_resp.json()
        assert results_data["status"] == "completed"
        assert results_data["total_expected_cases"] == 2
        assert results_data["completed_cases"] == 1
        assert results_data["failed_cases"] == 1
        assert len(results_data["items"]) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_atomic_claim_skips_duplicate_delivery() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [{"case_id": case_ids[0], "response": "Expected Answer 0"}],
        }

        post_resp = await client.post("/api/v1/evaluations/runs", json=payload)
        run_id = post_resp.json()["id"]

        ctx = {
            "session_factory": session_factory,
            "worker_id": "evalx-worker-claim-test",
            "job_id": f"job-{run_id}",
            "job_try": 1,
        }

        # First execution: claims run and executes successfully
        await execute_run_task(ctx, run_id=run_id, correlation_id="corr-claim-1")

        async with session_factory() as session:
            run_after_1 = await session.get(EvaluationRun, uuid.UUID(run_id))
            assert run_after_1 is not None
            assert run_after_1.status == RunStatus.COMPLETED.value
            completed_at_1 = run_after_1.completed_at

        # Second delivery (e.g. duplicate ARQ delivery or redelivery)
        # Must safely skip execution without modifying run or re-running evaluators
        await execute_run_task(ctx, run_id=run_id, correlation_id="corr-claim-2")

        async with session_factory() as session:
            run_after_2 = await session.get(EvaluationRun, uuid.UUID(run_id))
            assert run_after_2 is not None
            assert run_after_2.status == RunStatus.COMPLETED.value
            assert run_after_2.completed_at == completed_at_1

    await engine.dispose()


# =============================================================================
# 5. WORKER RETRY POLICY & ERROR HANDLING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_worker_non_retryable_error_fails_immediately() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [{"case_id": case_ids[0], "response": "Expected Answer 0"}],
        }

        post_resp = await client.post("/api/v1/evaluations/runs", json=payload)
        run_id = post_resp.json()["id"]

        ctx = {
            "session_factory": session_factory,
            "worker_id": "evalx-worker-non-retry",
            "job_id": f"job-{run_id}",
            "job_try": 1,
        }

        # Mock execute_evaluation_run to raise an UnsupportedEvaluatorError
        with patch(
            "app.services.evaluation_run_service.execute_evaluation_run",
            side_effect=UnsupportedEvaluatorError(
                "Unknown evaluator type: test_invalid"
            ),
        ):
            # Non-retryable error must NOT raise arq Retry exception
            await execute_run_task(ctx, run_id=run_id, correlation_id="corr-non-retry")

        # Run in DB must be marked FAILED immediately
        async with session_factory() as session:
            run = await session.get(EvaluationRun, uuid.UUID(run_id))
            assert run is not None
            assert run.status == RunStatus.FAILED.value
            assert "Unknown evaluator type" in (run.error_message or "")

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_transient_error_retries_then_fails() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [{"case_id": case_ids[0], "response": "Expected Answer 0"}],
        }

        post_resp = await client.post("/api/v1/evaluations/runs", json=payload)
        run_id = post_resp.json()["id"]

        # 1. Attempt 1 (< worker_max_retries): raises Retry
        ctx_attempt_1 = {
            "session_factory": session_factory,
            "worker_id": "evalx-worker-retry",
            "job_id": f"job-{run_id}",
            "job_try": 1,
        }

        with patch(
            "app.services.evaluation_run_service.execute_evaluation_run",
            side_effect=OperationalError(
                "Connection lost", params={}, orig=Exception("DB drop")
            ),
        ):
            with pytest.raises(Retry) as retry_exc:
                await execute_run_task(
                    ctx_attempt_1, run_id=run_id, correlation_id="corr-retry"
                )
            assert retry_exc.value.defer_score is not None

        # 2. Final attempt (>= worker_max_retries): exhausts retries, marks run FAILED
        ctx_attempt_final = {
            "session_factory": session_factory,
            "worker_id": "evalx-worker-retry",
            "job_id": f"job-{run_id}",
            "job_try": settings.worker_max_retries + 1,
        }

        with patch(
            "app.services.evaluation_run_service.execute_evaluation_run",
            side_effect=OperationalError(
                "Connection lost permanently", params={}, orig=Exception("DB dead")
            ),
        ):
            # Retries exhausted: does NOT raise Retry, sets FAILED
            await execute_run_task(
                ctx_attempt_final, run_id=run_id, correlation_id="corr-retry"
            )

        async with session_factory() as session:
            run = await session.get(EvaluationRun, uuid.UUID(run_id))
            assert run is not None
            assert run.status == RunStatus.FAILED.value
            assert "retries exhausted" in (run.error_message or "").lower()

    await engine.dispose()


# =============================================================================
# 6. WORKER HEARTBEAT & HEALTH ENDPOINT TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_worker_heartbeat_write_and_check() -> None:
    try:
        redis = Redis.from_url(settings.redis_url)
        await redis.ping()
    except Exception:
        pytest.skip("Redis not reachable for live heartbeat test")

    worker_id = "test-worker-heartbeat-123"
    await write_worker_heartbeat(redis, worker_id)

    is_active, details = await check_worker_heartbeat(redis)
    assert is_active is True
    assert details["worker_id"] == worker_id
    assert "timestamp" in details
    assert details["ttl_remaining_seconds"] > 0

    await redis.aclose()


@pytest.mark.asyncio
async def test_worker_health_endpoint_healthy_when_heartbeat_active() -> None:
    class MockRedis:
        async def ping(self) -> bool:
            return True

        async def aclose(self) -> None:
            pass

    async def mock_heartbeat(_redis: Any) -> tuple[bool, dict[str, Any]]:
        return True, {"worker_id": "worker-unit-test", "ttl_remaining_seconds": 28}

    transport = ASGITransport(app=app)
    with patch("app.api.v1.health.check_worker_health") as mock_chk:
        mock_chk.return_value = {
            "status": "healthy",
            "redis_reachable": True,
            "worker_heartbeat": "active",
            "details": {"worker_id": "worker-unit-test"},
        }
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.get("/api/v1/health/worker")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["redis_reachable"] is True
            assert data["worker_heartbeat"] == "active"
            # Must not expose redis password/host
            assert "password" not in str(data).lower()


@pytest.mark.asyncio
async def test_worker_health_endpoint_degraded_when_heartbeat_stale() -> None:
    transport = ASGITransport(app=app)
    with patch("app.api.v1.health.check_worker_health") as mock_chk:
        mock_chk.return_value = {
            "status": "degraded",
            "redis_reachable": True,
            "worker_heartbeat": "stale",
            "details": {"error": "No worker heartbeat found (worker down or stopped)"},
        }
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.get("/api/v1/health/worker")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["redis_reachable"] is True
            assert data["worker_heartbeat"] == "stale"


@pytest.mark.asyncio
async def test_worker_health_endpoint_unhealthy_when_redis_unreachable() -> None:
    transport = ASGITransport(app=app)
    with patch("app.api.v1.health.check_worker_health") as mock_chk:
        mock_chk.return_value = {
            "status": "unhealthy",
            "redis_reachable": False,
            "worker_heartbeat": "unknown",
            "details": {"redis_error": "Connection refused"},
        }
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.get("/api/v1/health/worker")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "unhealthy"
            assert data["redis_reachable"] is False
            assert data["worker_heartbeat"] == "unknown"


@pytest.mark.asyncio
async def test_health_ready_returns_503_when_redis_unreachable() -> None:
    class HealthySession:
        async def execute(self, _statement: object) -> None:
            return None

    async def get_healthy_session() -> HealthySession:
        return HealthySession()

    app.dependency_overrides[get_async_session] = get_healthy_session
    transport = ASGITransport(app=app)

    try:
        with patch("redis.asyncio.Redis.from_url") as mock_redis_cls:
            mock_client = AsyncMock()
            mock_client.ping.side_effect = ConnectionError("Redis unreachable")
            mock_redis_cls.return_value = mock_client

            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                resp = await client.get("/api/v1/health/ready")
                assert resp.status_code == 503
                assert resp.json()["detail"] == "Redis unavailable"
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 7. RESULTS ENRICHED PROGRESS SCHEMA TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_results_endpoint_exposes_run_progress() -> None:
    queue = InMemoryEvaluationQueue()
    app.dependency_overrides[get_run_executor] = lambda: QueuedRunExecutor(queue=queue)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=2)

        payload = {
            "dataset_id": dataset_id,
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                    "name": "em",
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Expected Answer 0"},
                {"case_id": case_ids[1], "response": "Expected Answer 1"},
            ],
        }

        post_resp = await client.post("/api/v1/evaluations/runs", json=payload)
        assert post_resp.status_code == 202
        run_id = post_resp.json()["id"]

        # GET /results while run is PENDING
        results_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}/results")
        assert results_resp.status_code == 200
        data = results_resp.json()

        assert data["run_id"] == run_id
        assert data["status"] == "pending"
        assert data["total_expected_cases"] == 2
        assert data["completed_cases"] == 0
        assert data["failed_cases"] == 0
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert data["total_pages"] == 0
