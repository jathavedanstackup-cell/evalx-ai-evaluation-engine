import math
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.models.evaluation_configuration import (
    EvaluationConfig,
    EvaluationConfigVersion,
)
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.evaluation_config import (
    EvaluationConfigCreate,
    EvaluationConfigResponse,
    EvaluationConfigUpdate,
    EvaluationConfigVersionResponse,
    EvaluatorDefinitionResponse,
)
from app.security.rate_limiter import rate_limit_standard
from app.services import evaluation_config_service

router = APIRouter(prefix="/evaluation-configs", tags=["Evaluation Configurations"])


def _to_evaluators_response(
    evaluators_data: list[dict],
) -> list[EvaluatorDefinitionResponse]:
    return [
        EvaluatorDefinitionResponse(
            evaluator_type=e.get("evaluator_type", ""),
            backend=e.get("backend", "llm_judge"),
            threshold=e.get("threshold"),
            weight=float(e.get("weight", 1.0)),
            enabled=bool(e.get("enabled", True)),
            name=e.get("name"),
            configuration=e.get("configuration"),
        )
        for e in evaluators_data
    ]


def _to_config_response(
    config: EvaluationConfig,
) -> EvaluationConfigResponse:
    return EvaluationConfigResponse(
        id=config.id,
        name=config.name,
        description=config.description,
        version=config.version,
        owner_user_id=config.owner_user_id,
        evaluators=_to_evaluators_response(config.evaluators),
        snapshot_hash=config.snapshot_hash,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _to_version_response(
    v: EvaluationConfigVersion,
) -> EvaluationConfigVersionResponse:
    return EvaluationConfigVersionResponse(
        id=v.id,
        config_id=v.config_id,
        version=v.version,
        name=v.name,
        description=v.description,
        evaluators=_to_evaluators_response(v.evaluators),
        snapshot_hash=v.snapshot_hash,
        created_at=v.created_at,
    )


@router.post(
    "",
    response_model=EvaluationConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reusable evaluation configuration preset",
    dependencies=[Depends(rate_limit_standard)],
)
async def create_evaluation_config(
    data: EvaluationConfigCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EvaluationConfigResponse:
    config = await evaluation_config_service.create_configuration(
        session, data, owner_user_id=current_user.id
    )
    return _to_config_response(config)


@router.get(
    "",
    response_model=PaginatedResponse[EvaluationConfigResponse],
    status_code=status.HTTP_200_OK,
    summary="List evaluation configurations with deterministic pagination",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_evaluation_configs(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[
        int,
        Query(ge=1, description="Page number (1-based)"),
    ] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, description="Items per page (max 100)"),
    ] = 20,
    search: Annotated[
        str | None,
        Query(
            max_length=255,
            description="Optional substring filter on configuration name",
        ),
    ] = None,
) -> PaginatedResponse[EvaluationConfigResponse]:
    items, total = await evaluation_config_service.list_configurations(
        session,
        owner_user_id=current_user.id,
        page=page,
        page_size=page_size,
        search=search,
    )
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return PaginatedResponse(
        items=[_to_config_response(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{config_id}",
    response_model=EvaluationConfigResponse | EvaluationConfigVersionResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve evaluation configuration or historical version by ID",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_evaluation_config(
    config_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    version: Annotated[
        int | None,
        Query(ge=1, description="Optional historical version number"),
    ] = None,
) -> EvaluationConfigResponse | EvaluationConfigVersionResponse:
    result = await evaluation_config_service.get_configuration(
        session,
        config_id=config_id,
        owner_user_id=current_user.id,
        version=version,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation configuration '{config_id}' not found",
        )
    if isinstance(result, EvaluationConfigVersion):
        return _to_version_response(result)
    return _to_config_response(result)


@router.patch(
    "/{config_id}",
    response_model=EvaluationConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Update evaluation configuration (increments version)",
    dependencies=[Depends(rate_limit_standard)],
)
async def update_evaluation_config(
    config_id: UUID,
    data: EvaluationConfigUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EvaluationConfigResponse:
    config = await evaluation_config_service.update_configuration(
        session,
        config_id=config_id,
        data=data,
        owner_user_id=current_user.id,
    )
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation configuration '{config_id}' not found",
        )
    return _to_config_response(config)


@router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete evaluation configuration and its history",
    dependencies=[Depends(rate_limit_standard)],
)
async def delete_evaluation_config(
    config_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    deleted = await evaluation_config_service.delete_configuration(
        session,
        config_id=config_id,
        owner_user_id=current_user.id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation configuration '{config_id}' not found",
        )
