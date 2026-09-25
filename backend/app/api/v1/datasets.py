from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.dataset import DatasetCreate, DatasetResponse, DatasetUpdate
from app.schemas.dataset_case import (
    DatasetCaseBulkCreateRequest,
    DatasetCaseBulkCreateResponse,
    DatasetCaseCreate,
    DatasetCaseResponse,
    DatasetCaseUpdate,
)
from app.schemas.validation import DatasetValidationResult
from app.security.rate_limiter import rate_limit_bulk, rate_limit_standard
from app.services import dataset_service

router = APIRouter(prefix="/datasets", tags=["Datasets"])


def _to_dataset_response(dataset: Dataset, case_count: int = 0) -> DatasetResponse:
    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        version=dataset.version,
        owner_user_id=dataset.owner_user_id,
        case_count=case_count,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


@router.post(
    "",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new dataset",
    dependencies=[Depends(rate_limit_standard)],
)
async def create_dataset(
    data: DatasetCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetResponse:
    dataset = await dataset_service.create_dataset(
        session, data, owner_user_id=current_user.id
    )
    return _to_dataset_response(dataset, case_count=0)


@router.get(
    "",
    response_model=PaginatedResponse[DatasetResponse],
    status_code=status.HTTP_200_OK,
    summary="List datasets with pagination and optional search",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_datasets(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[
        int,
        Query(
            ge=1,
            le=10_000,
            description="Page number (bounded pagination policy: 1-10000)",
        ),
    ] = 1,
    page_size: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Items per page (bounded pagination policy: 1-100)",
        ),
    ] = 20,
    search: Annotated[
        str | None,
        Query(
            max_length=100,
            description="Search filter for dataset name (max 100 characters)",
        ),
    ] = None,
) -> PaginatedResponse[DatasetResponse]:
    rows, total = await dataset_service.list_datasets(
        session,
        owner_user_id=current_user.id,
        page=page,
        page_size=page_size,
        search=search,
    )
    items = [_to_dataset_response(d, count) for d, count in rows]
    return PaginatedResponse.create(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get(
    "/{dataset_id}",
    response_model=DatasetResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a dataset by ID with total case count",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_dataset(
    dataset_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetResponse:
    result = await dataset_service.get_dataset_with_case_count(
        session, dataset_id, owner_user_id=current_user.id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    dataset, case_count = result
    return _to_dataset_response(dataset, case_count=case_count)


@router.patch(
    "/{dataset_id}",
    response_model=DatasetResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a dataset's name or description",
    dependencies=[Depends(rate_limit_standard)],
)
async def update_dataset(
    dataset_id: UUID,
    data: DatasetUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetResponse:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    updated = await dataset_service.update_dataset(session, dataset, data)
    result = await dataset_service.get_dataset_with_case_count(
        session, dataset_id, owner_user_id=current_user.id
    )
    case_count = result[1] if result else 0
    return _to_dataset_response(updated, case_count=case_count)


@router.delete(
    "/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dataset and all associated cases and evaluations",
    dependencies=[Depends(rate_limit_standard)],
)
async def delete_dataset(
    dataset_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    await dataset_service.delete_dataset(session, dataset)


@router.post(
    "/{dataset_id}/cases",
    response_model=DatasetCaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a single test case for a dataset",
    dependencies=[Depends(rate_limit_standard)],
)
async def create_dataset_case(
    dataset_id: UUID,
    data: DatasetCaseCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetCaseResponse:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    case = await dataset_service.create_dataset_case(session, dataset_id, data)
    return DatasetCaseResponse.model_validate(case)


@router.get(
    "/{dataset_id}/cases",
    response_model=PaginatedResponse[DatasetCaseResponse],
    status_code=status.HTTP_200_OK,
    summary="List test cases for a dataset with pagination",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_dataset_cases(
    dataset_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[
        int,
        Query(
            ge=1,
            le=10_000,
            description="Page number (bounded pagination policy: 1-10000)",
        ),
    ] = 1,
    page_size: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Items per page (bounded pagination policy: 1-100)",
        ),
    ] = 20,
) -> PaginatedResponse[DatasetCaseResponse]:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    cases, total = await dataset_service.list_dataset_cases(
        session, dataset_id, page=page, page_size=page_size
    )
    items = [DatasetCaseResponse.model_validate(c) for c in cases]
    return PaginatedResponse.create(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get(
    "/{dataset_id}/cases/{case_id}",
    response_model=DatasetCaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single dataset case by ID",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_dataset_case(
    dataset_id: UUID,
    case_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetCaseResponse:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    case = await dataset_service.get_dataset_case(session, dataset_id, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset case not found",
        )
    return DatasetCaseResponse.model_validate(case)


@router.patch(
    "/{dataset_id}/cases/{case_id}",
    response_model=DatasetCaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a single dataset case",
    dependencies=[Depends(rate_limit_standard)],
)
async def update_dataset_case(
    dataset_id: UUID,
    case_id: UUID,
    data: DatasetCaseUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetCaseResponse:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    case = await dataset_service.get_dataset_case(session, dataset_id, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset case not found",
        )
    updated = await dataset_service.update_dataset_case(session, case, data)
    return DatasetCaseResponse.model_validate(updated)


@router.delete(
    "/{dataset_id}/cases/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a single dataset case",
    dependencies=[Depends(rate_limit_standard)],
)
async def delete_dataset_case(
    dataset_id: UUID,
    case_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    case = await dataset_service.get_dataset_case(session, dataset_id, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset case not found",
        )
    await dataset_service.delete_dataset_case(session, case)


@router.post(
    "/{dataset_id}/cases/bulk",
    response_model=DatasetCaseBulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk create dataset cases in an atomic transaction (1-1000 items)",
    dependencies=[Depends(rate_limit_bulk)],
)
async def bulk_create_dataset_cases(
    dataset_id: UUID,
    payload: DatasetCaseBulkCreateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetCaseBulkCreateResponse:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    cases = await dataset_service.bulk_create_dataset_cases(
        session, dataset_id, payload.cases
    )
    return DatasetCaseBulkCreateResponse(
        dataset_id=dataset_id,
        inserted_count=len(cases),
        cases=[DatasetCaseResponse.model_validate(c) for c in cases],
    )


@router.post(
    "/{dataset_id}/validate",
    response_model=DatasetValidationResult,
    status_code=status.HTTP_200_OK,
    summary="Validate dataset completeness, duplicate detection, and schema compliance",
    dependencies=[Depends(rate_limit_standard)],
)
async def validate_dataset(
    dataset_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DatasetValidationResult:
    dataset = await dataset_service.get_dataset(
        session, dataset_id, owner_user_id=current_user.id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    return await dataset_service.validate_dataset(session, dataset_id)
