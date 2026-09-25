"""Worker and run reconciliation utilities (Step 16).

Detects abandoned or orphaned runs stuck in RUNNING or IN_PROGRESS state
due to worker crashes, ungraceful container termination, or node evictions,
and safely marks them FAILED while emitting structured observability events.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.evaluation.lifecycle import RunStatus
from app.models.evaluation_run import EvaluationRun
from app.observability.events import EvaluationEvent, EvaluationEventType
from app.observability.observability import EvaluationObservability
from app.services import audit_service

logger = logging.getLogger(__name__)


async def reconcile_stale_runs(
    session: AsyncSession,
    stale_timeout_hours: int | None = None,
    observability: EvaluationObservability | None = None,
) -> int:
    """Finds and transitions orphaned evaluation runs stuck in active states.

    Runs that started longer than `stale_timeout_hours` ago and never finished
    are marked as FAILED with an explicit explanatory message, preventing
    permanent orphaned runs.
    """
    timeout_hours = stale_timeout_hours or settings.stale_run_timeout_hours
    cutoff = datetime.now(UTC) - timedelta(hours=timeout_hours)
    obs = observability or EvaluationObservability()

    stmt = select(EvaluationRun).where(
        EvaluationRun.status.in_([RunStatus.RUNNING.value, "IN_PROGRESS", "CLAIMED"]),
        EvaluationRun.started_at.is_not(None),
        EvaluationRun.started_at < cutoff,
    )
    stale_runs = (await session.scalars(stmt)).all()

    reconciled_count = 0
    now = datetime.now(UTC)
    for run in stale_runs:
        logger.warning(
            "Reconciling stale evaluation run %s (started at %s, cutoff %s)",
            run.id,
            run.started_at,
            cutoff,
        )
        run.status = RunStatus.FAILED.value
        run.completed_at = now
        run.error_message = (
            f"Execution timed out or worker stalled after {timeout_hours}h "
            "without completion."
        )
        if run.started_at:
            run.duration_ms = (now - run.started_at).total_seconds() * 1000.0

        cid = f"reconcile-{run.id}"
        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.RUN_FAILED,
                correlation_id=cid,
                actor_user_id=None,
                owner_user_id=None,
                run_id=run.id,
                error=run.error_message,
            )
        )
        await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.RUN_FAILED,
            correlation_id=cid,
            resource_type="evaluation_run",
            resource_id=str(run.id),
            run_id=run.id,
            outcome="failure",
            metadata={
                "reason": "stale_run_reconciliation",
                "timeout_hours": timeout_hours,
            },
        )
        reconciled_count += 1

    if reconciled_count > 0:
        await session.commit()
        logger.info(
            "Successfully reconciled %d stale evaluation runs.", reconciled_count
        )

    return reconciled_count
