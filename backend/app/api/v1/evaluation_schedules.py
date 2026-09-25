"""API endpoints for Evaluation Automation & Scheduling (Step 15).

Provides versioned endpoints for creating, inspecting, updating, toggling,
triggering, and querying execution histories of recurring evaluation schedules.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.models.user import User
from app.observability.correlation import validate_and_normalize_correlation_id
from app.schemas.common import PaginatedResponse
from app.schemas.evaluation_schedule import (
    EvaluationScheduleCreate,
    EvaluationScheduleResponse,
    EvaluationScheduleUpdate,
    ScheduleExecutionResponse,
)
from app.security.rate_limiter import rate_limit_standard
from app.services import scheduler_service
from app.services.run_executor import RunExecutor

router = APIRouter(
    prefix="/evaluation-schedules",
    tags=["Evaluation Automation & Scheduling"],
    dependencies=[Depends(rate_limit_standard)],
)


@router.post(
    "",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an evaluation schedule",
)
async def create_schedule(
    data: EvaluationScheduleCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationScheduleResponse:
    """Creates a new evaluation schedule scoped strictly to the authenticated tenant."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    schedule = await scheduler_service.create_schedule(
        session=session,
        data=data,
        owner_user_id=current_user.id,
        correlation_id=valid_cid,
    )
    return EvaluationScheduleResponse.model_validate(schedule)


@router.get(
    "",
    response_model=PaginatedResponse[EvaluationScheduleResponse],
    status_code=status.HTTP_200_OK,
    summary="List tenant evaluation schedules",
)
async def list_schedules(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    enabled: Annotated[
        bool | None,
        Query(description="Filter by enabled status"),
    ] = None,
    schedule_type: Annotated[
        str | None,
        Query(description="Filter by schedule recurrence type"),
    ] = None,
    page: Annotated[int, Query(ge=1, le=10_000, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> PaginatedResponse[EvaluationScheduleResponse]:
    """Retrieves paginated schedules for the authenticated tenant."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    schedules, total = await scheduler_service.list_schedules(
        session=session,
        owner_user_id=current_user.id,
        enabled=enabled,
        schedule_type=schedule_type,
        page=page,
        page_size=page_size,
    )
    items = [EvaluationScheduleResponse.model_validate(s) for s in schedules]
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{schedule_id}",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve an evaluation schedule",
)
async def get_schedule(
    schedule_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationScheduleResponse:
    """Retrieves a single evaluation schedule by UUID (404 on cross-tenant access)."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    schedule = await scheduler_service.get_schedule(
        session=session,
        schedule_id=schedule_id,
        owner_user_id=current_user.id,
    )
    return EvaluationScheduleResponse.model_validate(schedule)


@router.patch(
    "/{schedule_id}",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an evaluation schedule",
)
async def update_schedule(
    schedule_id: UUID,
    data: EvaluationScheduleUpdate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationScheduleResponse:
    """Updates an evaluation schedule and recomputes the next run time."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    schedule = await scheduler_service.update_schedule(
        session=session,
        schedule_id=schedule_id,
        data=data,
        owner_user_id=current_user.id,
        correlation_id=valid_cid,
    )
    return EvaluationScheduleResponse.model_validate(schedule)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an evaluation schedule",
)
async def delete_schedule(
    schedule_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> None:
    """Deletes an evaluation schedule while preserving all past evaluation runs."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    await scheduler_service.delete_schedule(
        session=session,
        schedule_id=schedule_id,
        owner_user_id=current_user.id,
        correlation_id=valid_cid,
    )


@router.post(
    "/{schedule_id}/enable",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable an evaluation schedule",
)
@router.post(
    "/{schedule_id}/resume",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume an evaluation schedule",
)
async def enable_schedule(
    schedule_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationScheduleResponse:
    """Enables a schedule and calculates its next due execution time."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    schedule = await scheduler_service.enable_schedule(
        session=session,
        schedule_id=schedule_id,
        owner_user_id=current_user.id,
        correlation_id=valid_cid,
    )
    return EvaluationScheduleResponse.model_validate(schedule)


@router.post(
    "/{schedule_id}/disable",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable an evaluation schedule",
)
@router.post(
    "/{schedule_id}/pause",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_200_OK,
    summary="Pause an evaluation schedule",
)
async def disable_schedule(
    schedule_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationScheduleResponse:
    """Disables a schedule and pauses future automatic triggers."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    schedule = await scheduler_service.disable_schedule(
        session=session,
        schedule_id=schedule_id,
        owner_user_id=current_user.id,
        correlation_id=valid_cid,
    )
    return EvaluationScheduleResponse.model_validate(schedule)


@router.get(
    "/{schedule_id}/runs",
    response_model=PaginatedResponse[ScheduleExecutionResponse],
    status_code=status.HTTP_200_OK,
    summary="List execution history for a schedule",
)
@router.get(
    "/{schedule_id}/executions",
    response_model=PaginatedResponse[ScheduleExecutionResponse],
    status_code=status.HTTP_200_OK,
    summary="List execution history for a schedule",
)
async def list_schedule_executions(
    schedule_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1, le=10_000, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> PaginatedResponse[ScheduleExecutionResponse]:
    """Retrieves paginated execution history records for a specific schedule."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    executions, total = await scheduler_service.list_schedule_executions(
        session=session,
        schedule_id=schedule_id,
        owner_user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    items = [ScheduleExecutionResponse.model_validate(e) for e in executions]
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{schedule_id}/runs/{execution_id}",
    response_model=ScheduleExecutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get single execution history record",
)
@router.get(
    "/{schedule_id}/executions/{execution_id}",
    response_model=ScheduleExecutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get single execution history record",
)
async def get_schedule_execution(
    schedule_id: UUID,
    execution_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> ScheduleExecutionResponse:
    """Retrieves a single execution history record for a schedule."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    execution = await scheduler_service.get_schedule_execution(
        session=session,
        schedule_id=schedule_id,
        execution_id=execution_id,
        owner_user_id=current_user.id,
    )
    return ScheduleExecutionResponse.model_validate(execution)


@router.post(
    "/{schedule_id}/trigger",
    response_model=ScheduleExecutionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Manually trigger an immediate schedule execution",
)
async def trigger_schedule(
    schedule_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    run_executor: Annotated[RunExecutor, Depends(get_run_executor)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> ScheduleExecutionResponse:
    """Manually triggers an immediate run using the standard run queue."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    execution = await scheduler_service.trigger_schedule(
        session=session,
        schedule_id=schedule_id,
        owner_user_id=current_user.id,
        run_executor=run_executor,
        correlation_id=valid_cid,
    )
    return ScheduleExecutionResponse.model_validate(execution)
