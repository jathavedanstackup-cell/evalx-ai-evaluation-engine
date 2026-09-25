from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.contracts import BaseEvaluator
from app.infrastructure.queue import EvaluationQueue
from app.models.evaluation_run import EvaluationRun
from app.observability import EvaluationObservability
from app.schemas.evaluation_run import EvaluationRunCreate


class RunExecutor(Protocol):
    """Explicit execution abstraction separating run submission from execution."""

    async def submit_and_execute(
        self,
        session: AsyncSession,
        data: EvaluationRunCreate,
        owner_user_id: UUID | None = None,
        correlation_id: str | None = None,
        evaluator_overrides: Sequence[BaseEvaluator] | None = None,
    ) -> EvaluationRun:
        """Submits and executes a run according to the executor strategy."""
        ...


class SynchronousRunExecutor:
    """Synchronous in-process run executor for Step 05B.

    Coordinates submit_evaluation_run and execute_evaluation_run within
    a single request boundary, establishing the clean separation required
    for background worker execution without duplicate orchestration.
    """

    def __init__(self, observability: EvaluationObservability | None = None) -> None:
        self._observability = observability or EvaluationObservability()

    @property
    def observability(self) -> EvaluationObservability:
        return self._observability

    async def submit_and_execute(
        self,
        session: AsyncSession,
        data: EvaluationRunCreate,
        owner_user_id: UUID | None = None,
        correlation_id: str | None = None,
        evaluator_overrides: Sequence[BaseEvaluator] | None = None,
    ) -> EvaluationRun:
        from app.services import evaluation_run_service

        # 1. Submit run: validates and records PENDING state
        run = await evaluation_run_service.submit_evaluation_run(
            session=session,
            data=data,
            owner_user_id=owner_user_id,
            correlation_id=correlation_id,
            observability=self._observability,
        )

        # 2. Execute run: transitions to RUNNING, executes cases, atomically commits
        return await evaluation_run_service.execute_evaluation_run(
            session=session,
            run_id=run.id,
            data=data,
            correlation_id=correlation_id,
            evaluator_overrides=evaluator_overrides,
            observability=self._observability,
        )


class QueuedRunExecutor:
    """Asynchronous production run executor for Step 06.

    Submits the run to PostgreSQL as PENDING and enqueues the minimal
    job payload (run_id, correlation_id) to the background queue.
    Returns the PENDING EvaluationRun immediately for 202 Accepted response.
    """

    def __init__(
        self,
        queue: EvaluationQueue | None = None,
        observability: EvaluationObservability | None = None,
    ) -> None:
        from app.infrastructure.queue import RedisEvaluationQueue

        self._queue: EvaluationQueue = queue or RedisEvaluationQueue()
        self._observability = observability or EvaluationObservability()

    @property
    def queue(self) -> EvaluationQueue:
        return self._queue

    @property
    def observability(self) -> EvaluationObservability:
        return self._observability

    async def submit_and_execute(
        self,
        session: AsyncSession,
        data: EvaluationRunCreate,
        owner_user_id: UUID | None = None,
        correlation_id: str | None = None,
        evaluator_overrides: Sequence[BaseEvaluator] | None = None,
    ) -> EvaluationRun:
        from app.evaluation.errors import RunEnqueueError, sanitize_error_message
        from app.evaluation.lifecycle import RunStatus
        from app.observability import EvaluationEvent, EvaluationEventType
        from app.services import evaluation_run_service

        # 1. Submit run: validates bounds, calculates snapshot hash, persists PENDING
        run = await evaluation_run_service.submit_evaluation_run(
            session=session,
            data=data,
            owner_user_id=owner_user_id,
            correlation_id=correlation_id,
            observability=self._observability,
        )

        cid = run.correlation_id or (correlation_id or "unknown")

        # 2. Enqueue minimal job payload to background worker queue
        try:
            await self._queue.enqueue(run_id=run.id, correlation_id=cid)
            self._observability.emit(
                EvaluationEvent(
                    event_type=EvaluationEventType.QUEUE_SUBMITTED,
                    run_id=run.id,
                    correlation_id=cid,
                )
            )
        except Exception as exc:
            self._observability.emit(
                EvaluationEvent(
                    event_type=EvaluationEventType.QUEUE_FAILED,
                    run_id=run.id,
                    correlation_id=cid,
                    error=str(exc),
                )
            )
            # Enqueue failure handling:
            # Mark run FAILED in DB to prevent orphaned runnable PENDING runs
            sanitized_err = sanitize_error_message(
                f"Failed to enqueue evaluation run to background queue: {exc}"
            )
            run.status = RunStatus.FAILED.value
            run.error_message = sanitized_err
            run.duration_ms = 0.0
            await session.commit()

            self._observability.emit(
                EvaluationEvent(
                    event_type=EvaluationEventType.RUN_FAILED,
                    run_id=run.id,
                    dataset_id=data.dataset_id,
                    correlation_id=cid,
                    status=RunStatus.FAILED.value,
                    duration_ms=0.0,
                    error=sanitized_err,
                )
            )

            # Raise domain/infrastructure exception — NEVER FastAPI HTTPException!
            if isinstance(exc, RunEnqueueError):
                raise exc
            raise RunEnqueueError(sanitized_err) from exc

        # 3. Successful enqueue: return PENDING run immediately
        return run
