"""Service layer for Evaluation Automation & Scheduling (Step 15).

Handles schedule lifecycle management, concurrency-safe FOR UPDATE SKIP LOCKED
claiming, bounded catch-up for missed runs, and integration with the evaluation
execution engine and queue.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.evaluation import Evaluation
from app.models.evaluation_configuration import (
    EvaluationConfig,
    EvaluationConfigVersion,
)
from app.models.evaluation_run import EvaluationRun
from app.models.evaluation_schedule import EvaluationSchedule, ScheduleExecution
from app.models.regression_gate import RegressionGate
from app.observability import (
    EvaluationEvent,
    EvaluationEventType,
    EvaluationObservability,
    validate_and_normalize_correlation_id,
)
from app.schemas.evaluation_run import EvaluationRunCreate
from app.schemas.evaluation_schedule import (
    EvaluationScheduleCreate,
    EvaluationScheduleUpdate,
    ScheduleExecutionStatus,
    ScheduleStatus,
    ScheduleType,
    compute_next_run_at,
)
from app.services import audit_service
from app.services.run_executor import QueuedRunExecutor, RunExecutor

logger = logging.getLogger(__name__)


async def _validate_schedule_entity_ownership(
    session: AsyncSession,
    owner_user_id: UUID,
    dataset_id: UUID,
    configuration_id: UUID | None = None,
    configuration_version: int | None = None,
    baseline_run_id: UUID | None = None,
    gate_id: UUID | None = None,
) -> None:
    """Ensures all referenced resources exist and belong strictly to the tenant."""
    # 1. Dataset check
    ds_stmt = select(Dataset).where(
        Dataset.id == dataset_id,
        Dataset.owner_user_id == owner_user_id,
    )
    dataset = (await session.scalars(ds_stmt)).first()
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )

    # 2. Configuration check
    if configuration_id is not None:
        cfg_stmt = select(EvaluationConfig).where(
            EvaluationConfig.id == configuration_id,
            EvaluationConfig.owner_user_id == owner_user_id,
        )
        cfg = (await session.scalars(cfg_stmt)).first()
        if not cfg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Evaluation configuration not found",
            )
        if configuration_version is not None:
            ver_stmt = select(EvaluationConfigVersion).where(
                EvaluationConfigVersion.config_id == configuration_id,
                EvaluationConfigVersion.version == configuration_version,
            )
            ver = (await session.scalars(ver_stmt)).first()
            if not ver:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Configuration version {configuration_version} not found",
                )

    # 3. Baseline run check
    if baseline_run_id is not None:
        base_run = await session.get(EvaluationRun, baseline_run_id)
        if not base_run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Baseline evaluation run not found",
            )
        eval_entity = await session.get(Evaluation, base_run.evaluation_id)
        if not eval_entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Baseline evaluation run not found",
            )
        base_ds = await session.get(Dataset, eval_entity.dataset_id)
        if not base_ds or base_ds.owner_user_id != owner_user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Baseline evaluation run not found",
            )

    # 4. Regression gate check
    if gate_id is not None:
        gate_stmt = select(RegressionGate).where(
            RegressionGate.id == gate_id,
            RegressionGate.owner_user_id == owner_user_id,
        )
        gate = (await session.scalars(gate_stmt)).first()
        if not gate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Regression gate not found",
            )


async def create_schedule(
    session: AsyncSession,
    data: EvaluationScheduleCreate,
    owner_user_id: UUID,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> EvaluationSchedule:
    """Creates a new evaluation schedule with boundary validation."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)

    # Enforce active schedule tenant limits if enabled
    if data.enabled:
        active_count_stmt = select(func.count(EvaluationSchedule.id)).where(
            EvaluationSchedule.owner_user_id == owner_user_id,
            EvaluationSchedule.enabled.is_(True),
        )
        active_count = await session.scalar(active_count_stmt) or 0
        if active_count >= settings.max_active_schedules_per_tenant:
            max_schedules = settings.max_active_schedules_per_tenant
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Maximum active schedules limit ({max_schedules}) "
                    "reached for tenant"
                ),
            )

    # Validate ownership of related resources
    await _validate_schedule_entity_ownership(
        session=session,
        owner_user_id=owner_user_id,
        dataset_id=data.dataset_id,
        configuration_id=data.configuration_id,
        configuration_version=data.configuration_version,
        baseline_run_id=data.baseline_run_id,
        gate_id=data.gate_id,
    )

    next_run = None
    if data.enabled:
        next_run = compute_next_run_at(
            schedule_type=data.schedule_type,
            definition=data.schedule_definition,
        )

    schedule = EvaluationSchedule(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name=data.name.strip(),
        description=data.description.strip() if data.description else None,
        enabled=data.enabled,
        schedule_type=data.schedule_type.value,
        schedule_definition=data.schedule_definition.model_dump(),
        dataset_id=data.dataset_id,
        configuration_id=data.configuration_id,
        configuration_version=data.configuration_version,
        baseline_run_id=data.baseline_run_id,
        gate_id=data.gate_id,
        next_run_at=next_run,
        last_status=ScheduleStatus.PENDING.value,
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_CREATED,
            correlation_id=cid,
            actor_user_id=owner_user_id,
            owner_user_id=owner_user_id,
            schedule_id=schedule.id,
            resource_type="schedule",
            resource_id=str(schedule.id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_CREATED,
        correlation_id=cid,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule.id),
        outcome="success",
    )
    await session.commit()
    return schedule


async def get_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    owner_user_id: UUID,
) -> EvaluationSchedule:
    """Retrieves a single schedule enforcing tenant isolation."""
    stmt = select(EvaluationSchedule).where(
        EvaluationSchedule.id == schedule_id,
        EvaluationSchedule.owner_user_id == owner_user_id,
    )
    schedule = (await session.scalars(stmt)).first()
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation schedule not found",
        )
    return schedule


async def list_schedules(
    session: AsyncSession,
    owner_user_id: UUID,
    enabled: bool | None = None,
    schedule_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[EvaluationSchedule], int]:
    """Retrieves paginated schedules for the authenticated tenant."""
    query = select(EvaluationSchedule).where(
        EvaluationSchedule.owner_user_id == owner_user_id
    )
    if enabled is not None:
        query = query.where(EvaluationSchedule.enabled == enabled)
    if schedule_type:
        query = query.where(EvaluationSchedule.schedule_type == schedule_type)

    total_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(total_query) or 0

    paginated_query = (
        query.order_by(EvaluationSchedule.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await session.scalars(paginated_query)).all()
    return list(items), total


async def update_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    data: EvaluationScheduleUpdate,
    owner_user_id: UUID,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> EvaluationSchedule:
    """Updates schedule, re-validating bounds and recalculating next_run."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)

    schedule = await get_schedule(session, schedule_id, owner_user_id)

    # Check tenant active schedule limits if enabling
    if data.enabled is True and not schedule.enabled:
        active_count_stmt = select(func.count(EvaluationSchedule.id)).where(
            EvaluationSchedule.owner_user_id == owner_user_id,
            EvaluationSchedule.enabled.is_(True),
        )
        active_count = await session.scalar(active_count_stmt) or 0
        if active_count >= settings.max_active_schedules_per_tenant:
            max_schedules = settings.max_active_schedules_per_tenant
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Maximum active schedules limit ({max_schedules}) "
                    "reached for tenant"
                ),
            )

    # Validate relationships if changed
    new_dataset_id = data.dataset_id or schedule.dataset_id
    new_config_id = (
        data.configuration_id
        if data.configuration_id is not None
        else schedule.configuration_id
    )
    new_config_version = (
        data.configuration_version
        if data.configuration_version is not None
        else schedule.configuration_version
    )
    new_baseline_id = (
        data.baseline_run_id
        if data.baseline_run_id is not None
        else schedule.baseline_run_id
    )
    new_gate_id = data.gate_id if data.gate_id is not None else schedule.gate_id

    await _validate_schedule_entity_ownership(
        session=session,
        owner_user_id=owner_user_id,
        dataset_id=new_dataset_id,
        configuration_id=new_config_id,
        configuration_version=new_config_version,
        baseline_run_id=new_baseline_id,
        gate_id=new_gate_id,
    )

    if data.name is not None:
        schedule.name = data.name.strip()
    if data.description is not None:
        schedule.description = data.description.strip() if data.description else None
    if data.enabled is not None:
        schedule.enabled = data.enabled
    if data.schedule_type is not None:
        schedule.schedule_type = data.schedule_type.value
    if data.schedule_definition is not None:
        schedule.schedule_definition = data.schedule_definition.model_dump()
    if data.dataset_id is not None:
        schedule.dataset_id = data.dataset_id
    if data.configuration_id is not None:
        schedule.configuration_id = data.configuration_id
    if data.configuration_version is not None:
        schedule.configuration_version = data.configuration_version
    if data.baseline_run_id is not None:
        schedule.baseline_run_id = data.baseline_run_id
    if data.gate_id is not None:
        schedule.gate_id = data.gate_id

    # Recalculate next run if definition or enabled state changed
    if schedule.enabled:
        schedule.next_run_at = compute_next_run_at(
            schedule_type=schedule.schedule_type,
            definition=schedule.schedule_definition,
        )
    else:
        schedule.next_run_at = None

    schedule.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(schedule)

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_UPDATED,
            correlation_id=cid,
            actor_user_id=owner_user_id,
            owner_user_id=owner_user_id,
            schedule_id=schedule.id,
            resource_type="schedule",
            resource_id=str(schedule.id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_UPDATED,
        correlation_id=cid,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule.id),
        outcome="success",
    )
    await session.commit()
    return schedule


async def delete_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    owner_user_id: UUID,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> None:
    """Deletes an evaluation schedule while preserving all evaluation runs."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)

    schedule = await get_schedule(session, schedule_id, owner_user_id)
    await session.delete(schedule)
    await session.commit()

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_DELETED,
            correlation_id=cid,
            actor_user_id=owner_user_id,
            owner_user_id=owner_user_id,
            schedule_id=schedule_id,
            resource_type="schedule",
            resource_id=str(schedule_id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_DELETED,
        correlation_id=cid,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule_id),
        outcome="success",
    )
    await session.commit()


async def enable_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    owner_user_id: UUID,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> EvaluationSchedule:
    """Enables a schedule and recalculates its next execution time."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)

    schedule = await get_schedule(session, schedule_id, owner_user_id)
    if not schedule.enabled:
        active_count_stmt = select(func.count(EvaluationSchedule.id)).where(
            EvaluationSchedule.owner_user_id == owner_user_id,
            EvaluationSchedule.enabled.is_(True),
        )
        active_count = await session.scalar(active_count_stmt) or 0
        if active_count >= settings.max_active_schedules_per_tenant:
            max_schedules = settings.max_active_schedules_per_tenant
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Maximum active schedules limit ({max_schedules}) "
                    "reached for tenant"
                ),
            )

    schedule.enabled = True
    schedule.next_run_at = compute_next_run_at(
        schedule_type=schedule.schedule_type,
        definition=schedule.schedule_definition,
    )
    schedule.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(schedule)

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_ENABLED,
            correlation_id=cid,
            actor_user_id=owner_user_id,
            owner_user_id=owner_user_id,
            schedule_id=schedule.id,
            resource_type="schedule",
            resource_id=str(schedule.id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_ENABLED,
        correlation_id=cid,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule.id),
        outcome="success",
    )
    await session.commit()
    return schedule


async def disable_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    owner_user_id: UUID,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> EvaluationSchedule:
    """Disables a schedule and pauses future automatic executions."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)

    schedule = await get_schedule(session, schedule_id, owner_user_id)
    schedule.enabled = False
    schedule.next_run_at = None
    schedule.last_status = ScheduleStatus.PAUSED.value
    schedule.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(schedule)

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_DISABLED,
            correlation_id=cid,
            actor_user_id=owner_user_id,
            owner_user_id=owner_user_id,
            schedule_id=schedule.id,
            resource_type="schedule",
            resource_id=str(schedule.id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_DISABLED,
        correlation_id=cid,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule.id),
        outcome="success",
    )
    await session.commit()
    return schedule


async def list_schedule_executions(
    session: AsyncSession,
    schedule_id: UUID,
    owner_user_id: UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ScheduleExecution], int]:
    """Retrieves paginated execution history for a schedule."""
    # Ensure schedule exists and belongs to tenant
    await get_schedule(session, schedule_id, owner_user_id)

    query = select(ScheduleExecution).where(
        ScheduleExecution.schedule_id == schedule_id
    )
    total_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(total_query) or 0

    paginated_query = (
        query.order_by(ScheduleExecution.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await session.scalars(paginated_query)).all()
    return list(items), total


async def get_schedule_execution(
    session: AsyncSession,
    schedule_id: UUID,
    execution_id: UUID,
    owner_user_id: UUID,
) -> ScheduleExecution:
    """Retrieves a single execution record for a schedule owned by the tenant."""
    await get_schedule(session, schedule_id, owner_user_id)

    stmt = select(ScheduleExecution).where(
        ScheduleExecution.id == execution_id,
        ScheduleExecution.schedule_id == schedule_id,
    )
    execution = (await session.scalars(stmt)).first()
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule execution not found",
        )
    return execution


async def _execute_single_schedule(
    session: AsyncSession,
    schedule: EvaluationSchedule,
    run_executor: RunExecutor,
    correlation_id: str | None = None,
    is_manual: bool = False,
    observability: EvaluationObservability | None = None,
) -> ScheduleExecution:
    """Executes a single schedule run via the existing queue/run engine."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)
    now = datetime.now(UTC)

    # 1. Record execution history entry
    execution = ScheduleExecution(
        id=uuid4(),
        schedule_id=schedule.id,
        started_at=now,
        execution_status=ScheduleExecutionStatus.TRIGGERED.value,
        correlation_id=cid,
    )
    session.add(execution)
    schedule.last_status = ScheduleStatus.EXECUTING.value
    await session.commit()

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_EXECUTION_STARTED,
            correlation_id=cid,
            actor_user_id=schedule.owner_user_id,
            owner_user_id=schedule.owner_user_id,
            schedule_id=schedule.id,
            resource_type="schedule",
            resource_id=str(schedule.id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_EXECUTION_STARTED,
        correlation_id=cid,
        actor_user_id=schedule.owner_user_id,
        owner_user_id=schedule.owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule.id),
        outcome="success",
    )
    await session.commit()

    try:
        # 2. Build EvaluationRunCreate payload reusing configuration and dataset
        run_payload = EvaluationRunCreate(
            dataset_id=schedule.dataset_id,
            config_id=schedule.configuration_id,
            config_version=schedule.configuration_version,
            name=f"Scheduled run: {schedule.name}",
            execution_metadata={
                "schedule_id": str(schedule.id),
                "schedule_type": schedule.schedule_type,
                "is_manual": is_manual,
                "baseline_run_id": (
                    str(schedule.baseline_run_id) if schedule.baseline_run_id else None
                ),
                "gate_id": str(schedule.gate_id) if schedule.gate_id else None,
            },
        )

        # 3. Submit and execute run via provided RunExecutor
        run = await run_executor.submit_and_execute(
            session=session,
            data=run_payload,
            owner_user_id=schedule.owner_user_id,
            correlation_id=cid,
        )

        # 4. Update schedule and execution state on success
        execution.run_id = run.id
        execution.completed_at = datetime.now(UTC)
        execution.execution_status = ScheduleExecutionStatus.SUCCEEDED.value

        schedule.last_run_id = run.id
        schedule.last_run_at = now
        schedule.last_status = ScheduleStatus.SUCCEEDED.value

        # Advance next_run_at
        if schedule.schedule_type == ScheduleType.ONE_TIME.value:
            schedule.enabled = False
            schedule.next_run_at = None
        else:
            schedule.next_run_at = compute_next_run_at(
                schedule_type=schedule.schedule_type,
                definition=schedule.schedule_definition,
                from_time=now,
            )

        schedule.updated_at = datetime.now(UTC)
        await session.commit()

        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.SCHEDULE_EXECUTION_COMPLETED,
                correlation_id=cid,
                actor_user_id=schedule.owner_user_id,
                owner_user_id=schedule.owner_user_id,
                schedule_id=schedule.id,
                run_id=run.id,
                resource_type="schedule",
                resource_id=str(schedule.id),
            )
        )
        await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.SCHEDULE_EXECUTION_COMPLETED,
            correlation_id=cid,
            actor_user_id=schedule.owner_user_id,
            owner_user_id=schedule.owner_user_id,
            resource_type="schedule",
            resource_id=str(schedule.id),
            run_id=run.id,
            outcome="success",
        )
        await session.commit()
        return execution

    except Exception as exc:
        logger.warning(
            "Schedule execution failed for schedule %s: %s",
            schedule.id,
            exc,
            exc_info=True,
        )
        execution.completed_at = datetime.now(UTC)
        execution.execution_status = ScheduleExecutionStatus.FAILED.value
        execution.error_code = str(exc)[:64]

        schedule.last_status = ScheduleStatus.FAILED.value
        schedule.updated_at = datetime.now(UTC)
        # Advance next run even on failure so recurring schedule doesn't stick
        if schedule.schedule_type == ScheduleType.ONE_TIME.value:
            schedule.enabled = False
            schedule.next_run_at = None
        else:
            schedule.next_run_at = compute_next_run_at(
                schedule_type=schedule.schedule_type,
                definition=schedule.schedule_definition,
                from_time=now,
            )

        await session.commit()

        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.SCHEDULE_EXECUTION_FAILED,
                correlation_id=cid,
                actor_user_id=schedule.owner_user_id,
                owner_user_id=schedule.owner_user_id,
                schedule_id=schedule.id,
                resource_type="schedule",
                resource_id=str(schedule.id),
                error=str(exc),
            )
        )
        await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.SCHEDULE_EXECUTION_FAILED,
            correlation_id=cid,
            actor_user_id=schedule.owner_user_id,
            owner_user_id=schedule.owner_user_id,
            resource_type="schedule",
            resource_id=str(schedule.id),
            outcome="failure",
        )
        await session.commit()
        return execution


async def trigger_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    owner_user_id: UUID,
    run_executor: RunExecutor | None = None,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> ScheduleExecution:
    """Manually triggers an immediate execution of a schedule."""
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)
    executor = run_executor or QueuedRunExecutor(observability=obs)

    schedule = await get_schedule(session, schedule_id, owner_user_id)

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.SCHEDULE_TRIGGERED,
            correlation_id=cid,
            actor_user_id=owner_user_id,
            owner_user_id=owner_user_id,
            schedule_id=schedule.id,
            resource_type="schedule",
            resource_id=str(schedule.id),
        )
    )
    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.SCHEDULE_TRIGGERED,
        correlation_id=cid,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        resource_type="schedule",
        resource_id=str(schedule.id),
        outcome="success",
    )
    await session.commit()

    return await _execute_single_schedule(
        session=session,
        schedule=schedule,
        run_executor=executor,
        correlation_id=cid,
        is_manual=True,
        observability=obs,
    )


async def poll_and_execute_due_schedules(
    session: AsyncSession,
    run_executor: RunExecutor | None = None,
    batch_size: int = 10,
    now_time: datetime | None = None,
    observability: EvaluationObservability | None = None,
) -> int:
    """Polls and executes due schedules concurrency-safely using FOR UPDATE SKIP LOCKED.

    Implements bounded catch-up policy: if a schedule's next_run_at is older
    than scheduler_max_catchup_hours, it marks the execution as MISSED and
    jumps forward without executing unbounded historical runs.
    """
    obs = observability or EvaluationObservability()
    executor = run_executor or QueuedRunExecutor(observability=obs)
    now = now_time or datetime.now(UTC)
    catchup_cutoff = now - timedelta(hours=settings.scheduler_max_catchup_hours)

    # Concurrency-safe query: skips locked rows from other workers
    claim_query = (
        select(EvaluationSchedule)
        .where(
            EvaluationSchedule.enabled.is_(True),
            EvaluationSchedule.next_run_at.is_not(None),
            EvaluationSchedule.next_run_at <= now,
        )
        .order_by(EvaluationSchedule.next_run_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    schedules = (await session.scalars(claim_query)).all()
    if not schedules:
        return 0

    executed_count = 0
    for sched in schedules:
        cid = f"sched-{uuid4().hex[:12]}"

        # Check for missed schedule window
        if sched.next_run_at and sched.next_run_at < catchup_cutoff:
            # Mark missed and jump forward to prevent backlog explosion
            execution = ScheduleExecution(
                id=uuid4(),
                schedule_id=sched.id,
                started_at=now,
                completed_at=now,
                execution_status=ScheduleExecutionStatus.MISSED.value,
                error_code="SCHEDULE_WINDOW_EXPIRED",
                correlation_id=cid,
            )
            session.add(execution)
            sched.last_status = ScheduleStatus.MISSED.value
            sched.next_run_at = compute_next_run_at(
                schedule_type=sched.schedule_type,
                definition=sched.schedule_definition,
                from_time=now,
            )
            sched.updated_at = now
            await session.commit()

            obs.emit(
                EvaluationEvent(
                    event_type=EvaluationEventType.SCHEDULE_MISSED,
                    correlation_id=cid,
                    actor_user_id=sched.owner_user_id,
                    owner_user_id=sched.owner_user_id,
                    schedule_id=sched.id,
                    resource_type="schedule",
                    resource_id=str(sched.id),
                )
            )
            await audit_service.record_audit_event(
                session=session,
                event_type=EvaluationEventType.SCHEDULE_MISSED,
                correlation_id=cid,
                actor_user_id=sched.owner_user_id,
                owner_user_id=sched.owner_user_id,
                resource_type="schedule",
                resource_id=str(sched.id),
                outcome="failure",
            )
            await session.commit()
            continue

        # Normal due schedule execution
        await _execute_single_schedule(
            session=session,
            schedule=sched,
            run_executor=executor,
            correlation_id=cid,
            is_manual=False,
            observability=obs,
        )
        executed_count += 1

    return executed_count
