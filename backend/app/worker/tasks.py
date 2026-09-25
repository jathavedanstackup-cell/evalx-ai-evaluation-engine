import logging
import time
from typing import Any
from uuid import UUID

from arq.worker import Retry
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.database.session import async_session_factory
from app.evaluation.errors import (
    InvalidEvaluationInputError,
    UnsupportedEvaluatorError,
)
from app.evaluation.lifecycle import RunStatus
from app.models.evaluation_run import EvaluationRun
from app.observability.metrics import default_metrics_collector
from app.services import evaluation_run_service

logger = logging.getLogger(__name__)

NON_RETRYABLE_EXCEPTIONS = (
    HTTPException,
    UnsupportedEvaluatorError,
    InvalidEvaluationInputError,
    ValueError,
    KeyError,
)


async def execute_run_task(
    ctx: dict[str, Any],
    run_id: str,
    correlation_id: str,
) -> None:
    """Consumes and executes an enqueued evaluation run job.

    The worker delegates execution entirely to execute_evaluation_run(),
    which acts as the single source of truth for the atomic state transition
    (PENDING -> RUNNING). If another worker or retry has already claimed the run,
    it skips execution safely without duplicate processing.
    """
    run_uuid = UUID(run_id)
    attempt = int(ctx.get("job_try", 1))
    worker_id = str(ctx.get("worker_id", "worker-default"))
    job_id = str(ctx.get("job_id", f"evalx-run-{run_id}"))

    session_factory = ctx.get("session_factory")
    if session_factory is None:
        session_factory = async_session_factory()
    elif callable(session_factory) and not isinstance(
        session_factory, async_sessionmaker
    ):
        session_factory = session_factory()

    start_time = time.perf_counter()
    async with session_factory() as session:
        try:
            await evaluation_run_service.execute_evaluation_run(
                session=session,
                run_id=run_uuid,
                data=None,  # Reconstruct run parameters from database
                correlation_id=correlation_id,
                worker_id=worker_id,
                job_id=job_id,
                attempt=attempt,
            )
            dur_ms = (time.perf_counter() - start_time) * 1000.0
            default_metrics_collector.record_worker_execution(
                duration_ms=dur_ms, success=True, attempt=attempt
            )
        except NON_RETRYABLE_EXCEPTIONS as non_retryable_err:
            dur_ms = (time.perf_counter() - start_time) * 1000.0
            default_metrics_collector.record_worker_execution(
                duration_ms=dur_ms, success=False, attempt=attempt
            )
            logger.warning(
                "Terminal non-retryable error in run %s (job %s): %s",
                run_id,
                job_id,
                non_retryable_err,
            )
            try:
                run = await session.get(EvaluationRun, run_uuid)
                if run and run.status != RunStatus.FAILED.value:
                    run.status = RunStatus.FAILED.value
                    run.error_message = str(non_retryable_err)
                    run.duration_ms = 0.0
                    await session.commit()
            except Exception as mark_err:
                logger.error("Failed to mark run %s as FAILED: %s", run_id, mark_err)
            return
        except (
            OperationalError,
            ConnectionError,
            OSError,
            SQLAlchemyError,
        ) as trans_err:
            logger.warning(
                "Transient infrastructure failure in run %s attempt %d: %s",
                run_id,
                attempt,
                trans_err,
            )
            if attempt < settings.worker_max_retries:
                delay = settings.worker_retry_delay_seconds * attempt
                logger.info(
                    "Scheduling retry for run %s in %d seconds (attempt %d/%d)",
                    run_id,
                    delay,
                    attempt + 1,
                    settings.worker_max_retries,
                )
                raise Retry(defer=delay) from trans_err

            logger.error(
                "Retries exhausted (%d/%d) for run %s: %s",
                attempt,
                settings.worker_max_retries,
                run_id,
                trans_err,
            )
            # Final retry exhaustion: ensure run is marked FAILED in DB
            run = await session.get(EvaluationRun, run_uuid)
            if run and run.status != RunStatus.FAILED.value:
                run.status = RunStatus.FAILED.value
                run.error_message = (
                    f"Retries exhausted after infrastructure error: {trans_err}"
                )
                await session.commit()
            return
        except Exception as general_err:
            logger.exception(
                "Unexpected execution error in run %s: %s", run_id, general_err
            )
            run = await session.get(EvaluationRun, run_uuid)
            if run and run.status != RunStatus.FAILED.value:
                run.status = RunStatus.FAILED.value
                run.error_message = f"Unexpected execution failure: {general_err}"
                await session.commit()
            return
