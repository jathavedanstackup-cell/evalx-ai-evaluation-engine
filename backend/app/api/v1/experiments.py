from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.models.experiment import Experiment
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.experiment import (
    ExperimentCreate,
    ExperimentResponse,
    ExperimentUpdate,
)
from app.security.rate_limiter import rate_limit_standard
from app.services import experiment_service

router = APIRouter(prefix="/experiments", tags=["Evaluation Experiments"])


def _to_experiment_response(exp: Experiment) -> ExperimentResponse:
    return ExperimentResponse(
        id=exp.id,
        owner_user_id=exp.owner_user_id,
        name=exp.name,
        description=exp.description,
        configuration_id=exp.configuration_id,
        baseline_run_id=exp.baseline_run_id,
        status=exp.status,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
    )


@router.post(
    "",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an evaluation experiment",
    dependencies=[Depends(rate_limit_standard)],
)
async def create_experiment(
    data: ExperimentCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ExperimentResponse:
    exp = await experiment_service.create_experiment(
        session, data, owner_user_id=current_user.id
    )
    return _to_experiment_response(exp)


@router.get(
    "",
    response_model=PaginatedResponse[ExperimentResponse],
    status_code=status.HTTP_200_OK,
    summary="List evaluation experiments with pagination",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_experiments(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    search: str | None = Query(None, description="Search by experiment name"),
    status_filter: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
) -> PaginatedResponse[ExperimentResponse]:
    items, total = await experiment_service.list_experiments(
        session,
        owner_user_id=current_user.id,
        page=page,
        page_size=page_size,
        search=search,
        status_filter=status_filter,
    )
    responses = [_to_experiment_response(e) for e in items]
    return PaginatedResponse.create(
        items=responses, total=total, page=page, page_size=page_size
    )


@router.get(
    "/{experiment_id}",
    response_model=ExperimentResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch an evaluation experiment by ID",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_experiment(
    experiment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ExperimentResponse:
    exp = await experiment_service.get_experiment(
        session, experiment_id, owner_user_id=current_user.id
    )
    if exp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )
    return _to_experiment_response(exp)


@router.patch(
    "/{experiment_id}",
    response_model=ExperimentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an evaluation experiment",
    dependencies=[Depends(rate_limit_standard)],
)
async def update_experiment(
    experiment_id: UUID,
    data: ExperimentUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ExperimentResponse:
    exp = await experiment_service.update_experiment(
        session, experiment_id, data, owner_user_id=current_user.id
    )
    if exp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )
    return _to_experiment_response(exp)


@router.delete(
    "/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an evaluation experiment",
    dependencies=[Depends(rate_limit_standard)],
)
async def delete_experiment(
    experiment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    deleted = await experiment_service.delete_experiment(
        session, experiment_id, owner_user_id=current_user.id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )
