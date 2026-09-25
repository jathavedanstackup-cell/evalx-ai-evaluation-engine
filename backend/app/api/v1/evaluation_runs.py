import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.evaluation.errors import QueueUnavailableError, RunEnqueueError
from app.models.user import User
from app.observability import validate_and_normalize_correlation_id
from app.schemas.audit import RunAuditTrailResponse
from app.schemas.common import PaginatedResponse
from app.schemas.evaluation_comparison import RunComparisonResponse
from app.schemas.evaluation_run import (
    EvaluationRunCreate,
    EvaluationRunResponse,
    EvaluationRunResultsResponse,
    ReapStaleRunsResponse,
)
from app.schemas.failure_analysis import RunAnalysisResponse
from app.security.rate_limiter import rate_limit_runs, rate_limit_standard
from app.services import audit_service, evaluation_run_service, failure_analysis_service
from app.services.run_executor import (
    QueuedRunExecutor,
    RunExecutor,
    SynchronousRunExecutor,
)

router = APIRouter(prefix="/evaluations/runs", tags=["Evaluation Runs"])
logger = logging.getLogger(__name__)


def get_run_executor() -> RunExecutor:
    """Provides the configured RunExecutor strategy (default: QueuedRunExecutor)."""
    return QueuedRunExecutor()


@router.post(
    "",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Launch and execute an evaluation run",
    dependencies=[Depends(rate_limit_runs)],
)
async def create_evaluation_run(
    data: EvaluationRunCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    executor: Annotated[RunExecutor, Depends(get_run_executor)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationRunResponse:
    """Submits an evaluation run for asynchronous or synchronous execution."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    try:
        run = await executor.submit_and_execute(
            session=session,
            data=data,
            owner_user_id=current_user.id,
            correlation_id=valid_cid,
        )
    except (RunEnqueueError, QueueUnavailableError) as exc:
        logger.error(
            "Evaluation run enqueue failed for correlation_id=%s: %s",
            valid_cid,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evaluation queue is currently unavailable.",
        ) from exc

    # If an in-process synchronous executor was injected (e.g. in test suites),
    # return HTTP 201 Created to match synchronous contract.
    if isinstance(executor, SynchronousRunExecutor):
        response.status_code = status.HTTP_201_CREATED

    return evaluation_run_service.to_run_response(run, dataset_id=data.dataset_id)


@router.get(
    "/compare",
    response_model=RunComparisonResponse,
    status_code=status.HTTP_200_OK,
    summary="Compare two completed evaluation runs",
    dependencies=[Depends(rate_limit_standard)],
)
async def compare_evaluation_runs(
    base_run_id: Annotated[UUID, Query(description="Base evaluation run UUID")],
    target_run_id: Annotated[
        UUID, Query(description="Target evaluation run UUID to compare against base")
    ],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    threshold: Annotated[
        float,
        Query(
            ge=0.0,
            le=1.0,
            description=(
                "Delta threshold below which difference is classified as "
                "regression (default 0.0)"
            ),
        ),
    ] = 0.0,
) -> RunComparisonResponse:
    """Compares metrics, overall scores, and per-case results between runs."""
    return await evaluation_run_service.compare_evaluation_runs(
        session=session,
        base_run_id=base_run_id,
        target_run_id=target_run_id,
        owner_user_id=current_user.id,
        threshold=threshold,
    )


@router.post(
    "/maintenance/reap-stale",
    response_model=ReapStaleRunsResponse,
    status_code=status.HTTP_200_OK,
    summary="Reap stale runs exceeding timeout threshold",
    dependencies=[Depends(rate_limit_standard)],
)
async def reap_stale_runs(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    timeout_seconds: Annotated[
        int,
        Query(
            ge=60,
            le=86_400,
            description=(
                "Maximum running duration in seconds before considering a "
                "run stale (default 1800s)"
            ),
        ),
    ] = 1800,
    batch_size: Annotated[
        int,
        Query(
            ge=1,
            le=500,
            description="Maximum number of runs to reap in one batch (default 50)",
        ),
    ] = 50,
) -> ReapStaleRunsResponse:
    """Identifies and marks active runs exceeding duration threshold as FAILED."""
    reaped_ids = await evaluation_run_service.reap_stale_runs(
        session=session,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
    )
    return ReapStaleRunsResponse(
        reaped_count=len(reaped_ids),
        reaped_run_ids=reaped_ids,
    )


@router.get(
    "/{run_id}",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve an evaluation run by ID",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_evaluation_run(
    run_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EvaluationRunResponse:
    """Retrieves metadata and execution status for a specific evaluation run."""
    run = await evaluation_run_service.get_evaluation_run(
        session, run_id, owner_user_id=current_user.id
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EvaluationRun '{run_id}' not found",
        )
    if run.correlation_id:
        response.headers["X-Correlation-ID"] = run.correlation_id
    return evaluation_run_service.to_run_response(run)


@router.get(
    "/{run_id}/results",
    response_model=EvaluationRunResultsResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve granular case evaluation results for a run",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_evaluation_run_results(
    run_id: UUID,
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
    ] = 50,
) -> EvaluationRunResultsResponse:
    """Returns paginated case-level evaluation results associated with a run."""
    run = await evaluation_run_service.get_evaluation_run(
        session, run_id, owner_user_id=current_user.id
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EvaluationRun '{run_id}' not found",
        )

    results, total = await evaluation_run_service.get_evaluation_run_results(
        session=session,
        run_id=run_id,
        page=page,
        page_size=page_size,
    )

    items = [evaluation_run_service.to_result_response(r) for r in results]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return EvaluationRunResultsResponse(
        run_id=run.id,
        status=run.status,
        total_expected_cases=run.total_cases,
        completed_cases=run.completed_cases,
        failed_cases=run.failed_cases,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{run_id}/analysis",
    response_model=RunAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve failure analysis and insights for an evaluation run",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_evaluation_run_analysis(
    run_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    baseline_run_id: Annotated[
        UUID | None,
        Query(
            description="Optional baseline evaluation run UUID for regression insights"
        ),
    ] = None,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> RunAnalysisResponse:
    """Computes and returns failure analysis and insights for a completed run."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    return await failure_analysis_service.analyze_run_failures(
        session=session,
        run_id=run_id,
        owner_user_id=current_user.id,
        baseline_run_id=baseline_run_id,
        correlation_id=valid_cid,
    )


@router.get(
    "/{run_id}/audit",
    response_model=RunAuditTrailResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve chronological audit trail for an evaluation run",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_evaluation_run_audit(
    run_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> RunAuditTrailResponse:
    """Returns a deterministic chronological audit trail for the specified run."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    return await audit_service.get_run_audit_trail(
        session=session,
        run_id=run_id,
        owner_user_id=current_user.id,
    )


@router.post(
    "/{run_id}/cancel",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel an active or pending evaluation run",
    dependencies=[Depends(rate_limit_standard)],
)
async def cancel_evaluation_run(
    run_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EvaluationRunResponse:
    """Cancels a pending or running evaluation run for the authenticated owner."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid
    run = await evaluation_run_service.cancel_evaluation_run(
        session=session,
        run_id=run_id,
        owner_user_id=current_user.id,
        correlation_id=valid_cid,
    )
    return evaluation_run_service.to_run_response(run)


@router.get(
    "",
    response_model=PaginatedResponse[EvaluationRunResponse],
    status_code=status.HTTP_200_OK,
    summary="List evaluation runs with optional filtering",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_evaluation_runs(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    dataset_id: Annotated[
        UUID | None, Query(description="Filter by dataset UUID")
    ] = None,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            max_length=50,
            description="Filter by run status (max 50 chars)",
        ),
    ] = None,
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
) -> PaginatedResponse[EvaluationRunResponse]:
    """Returns a paginated list of evaluation runs."""
    runs, total = await evaluation_run_service.list_evaluation_runs(
        session=session,
        owner_user_id=current_user.id,
        dataset_id=dataset_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )

    items = [evaluation_run_service.to_run_response(r) for r in runs]
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
