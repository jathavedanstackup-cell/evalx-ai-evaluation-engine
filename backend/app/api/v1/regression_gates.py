from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.models.regression_gate import (
    RegressionGate,
    RegressionGateResult,
    RegressionGateVersion,
)
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.regression_gate import (
    GateEvaluateRequest,
    GateEvaluateResponse,
    GateOperator,
    GateOverallStatus,
    GateRuleResponse,
    GateRuleResult,
    GateSeverity,
    RegressionGateCreate,
    RegressionGateResponse,
    RegressionGateUpdate,
    RegressionGateVersionResponse,
    RuleEvaluationStatus,
)
from app.security.rate_limiter import rate_limit_standard
from app.services import regression_gate_service

router = APIRouter(prefix="/regression-gates", tags=["Regression Gates"])


def _to_gate_response(gate: RegressionGate) -> RegressionGateResponse:
    return RegressionGateResponse(
        id=gate.id,
        name=gate.name,
        description=gate.description,
        configuration_id=gate.configuration_id,
        version=gate.version,
        enabled=gate.enabled,
        rules=[
            GateRuleResponse(
                metric_name=str(r.get("metric_name", "")),
                operator=GateOperator(str(r.get("operator", "gte"))),
                threshold=float(r.get("threshold", 0.0)),
                is_delta=bool(r.get("is_delta", False)),
                severity=GateSeverity(
                    str(r.get("severity", GateSeverity.CRITICAL.value))
                ),
                enabled=bool(r.get("enabled", True)),
            )
            for r in gate.rules
        ],
        snapshot_hash=gate.snapshot_hash,
        created_at=gate.created_at,
        updated_at=gate.updated_at,
        owner_user_id=gate.owner_user_id,
    )


def _to_gate_version_response(
    v: RegressionGateVersion,
) -> RegressionGateVersionResponse:
    return RegressionGateVersionResponse(
        id=v.id,
        gate_id=v.gate_id,
        version=v.version,
        name=v.name,
        description=v.description,
        configuration_id=v.configuration_id,
        rules=[
            GateRuleResponse(
                metric_name=str(r.get("metric_name", "")),
                operator=GateOperator(str(r.get("operator", "gte"))),
                threshold=float(r.get("threshold", 0.0)),
                is_delta=bool(r.get("is_delta", False)),
                severity=GateSeverity(
                    str(r.get("severity", GateSeverity.CRITICAL.value))
                ),
                enabled=bool(r.get("enabled", True)),
            )
            for r in v.rules
        ],
        snapshot_hash=v.snapshot_hash,
        created_at=v.created_at,
    )


def _to_evaluate_response(res: RegressionGateResult) -> GateEvaluateResponse:
    return GateEvaluateResponse(
        id=res.id,
        gate_id=res.gate_id,
        gate_version=res.gate_version,
        target_run_id=res.target_run_id,
        baseline_run_id=res.baseline_run_id,
        overall_status=GateOverallStatus(str(res.overall_status)),
        rule_results=[
            GateRuleResult(
                metric_name=str(r["metric_name"]),
                operator=GateOperator(str(r["operator"])),
                threshold=float(r["threshold"]),
                is_delta=bool(r["is_delta"]),
                severity=GateSeverity(str(r["severity"])),
                target_value=r.get("target_value"),
                baseline_value=r.get("baseline_value"),
                delta=r.get("delta"),
                status=RuleEvaluationStatus(str(r["status"])),
                reason=r.get("reason"),
            )
            for r in res.rule_results
        ],
        summary=res.summary,
        snapshot_hash=res.snapshot_hash,
        correlation_id=res.correlation_id,
        evaluated_at=res.evaluated_at,
    )


@router.post(
    "",
    response_model=RegressionGateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new regression quality gate",
    dependencies=[Depends(rate_limit_standard)],
)
async def create_gate(
    data: RegressionGateCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RegressionGateResponse:
    gate = await regression_gate_service.create_regression_gate(
        session, data, owner_user_id=current_user.id
    )
    return _to_gate_response(gate)


@router.get(
    "",
    response_model=PaginatedResponse[RegressionGateResponse],
    status_code=status.HTTP_200_OK,
    summary="List regression gates with pagination",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_gates(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    search: str | None = Query(None, description="Search by gate name"),
) -> PaginatedResponse[RegressionGateResponse]:
    items, total = await regression_gate_service.list_regression_gates(
        session,
        owner_user_id=current_user.id,
        page=page,
        page_size=page_size,
        search=search,
    )
    responses = [_to_gate_response(g) for g in items]
    return PaginatedResponse.create(
        items=responses, total=total, page=page, page_size=page_size
    )


@router.get(
    "/evaluations/{evaluation_id}",
    response_model=GateEvaluateResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch a specific regression gate evaluation result",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_evaluation_result(
    evaluation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> GateEvaluateResponse:
    result = await regression_gate_service.get_gate_evaluation(
        session, evaluation_id, owner_user_id=current_user.id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gate evaluation result not found",
        )
    return _to_evaluate_response(result)


@router.get(
    "/{gate_id}",
    response_model=RegressionGateResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch a regression gate by ID",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_gate(
    gate_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RegressionGateResponse:
    gate = await regression_gate_service.get_regression_gate(
        session, gate_id, owner_user_id=current_user.id
    )
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regression gate not found",
        )
    return _to_gate_response(gate)


@router.patch(
    "/{gate_id}",
    response_model=RegressionGateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a regression gate and archive previous version",
    dependencies=[Depends(rate_limit_standard)],
)
async def update_gate(
    gate_id: UUID,
    data: RegressionGateUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RegressionGateResponse:
    gate = await regression_gate_service.update_regression_gate(
        session, gate_id, data, owner_user_id=current_user.id
    )
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regression gate not found",
        )
    return _to_gate_response(gate)


@router.delete(
    "/{gate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a regression gate",
    dependencies=[Depends(rate_limit_standard)],
)
async def delete_gate(
    gate_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    deleted = await regression_gate_service.delete_regression_gate(
        session, gate_id, owner_user_id=current_user.id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regression gate not found",
        )


@router.get(
    "/{gate_id}/versions",
    response_model=list[RegressionGateVersionResponse],
    status_code=status.HTTP_200_OK,
    summary="List all historical versions of a regression gate",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_versions(
    gate_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[RegressionGateVersionResponse]:
    versions = await regression_gate_service.list_gate_versions(
        session, gate_id, owner_user_id=current_user.id
    )
    if versions is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regression gate not found",
        )
    return [_to_gate_version_response(v) for v in versions]


@router.get(
    "/{gate_id}/versions/{version}",
    response_model=RegressionGateVersionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a specific historical version of a regression gate",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_version(
    gate_id: UUID,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RegressionGateVersionResponse:
    v = await regression_gate_service.get_gate_version(
        session, gate_id, version, owner_user_id=current_user.id
    )
    if v is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version} of regression gate {gate_id} not found",
        )
    return _to_gate_version_response(v)


@router.post(
    "/{gate_id}/evaluate",
    response_model=GateEvaluateResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate regression gate against a target run and optional baseline run",
    dependencies=[Depends(rate_limit_standard)],
)
async def evaluate_gate(
    gate_id: UUID,
    data: GateEvaluateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    x_correlation_id: str | None = Header(None, alias="X-Correlation-ID"),
) -> GateEvaluateResponse:
    result = await regression_gate_service.evaluate_regression_gate(
        session,
        gate_id=gate_id,
        data=data,
        owner_user_id=current_user.id,
        correlation_id=x_correlation_id,
    )
    return _to_evaluate_response(result)


@router.get(
    "/{gate_id}/evaluations",
    response_model=PaginatedResponse[GateEvaluateResponse],
    status_code=status.HTTP_200_OK,
    summary="List historical evaluation decisions for a regression gate",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_evaluations(
    gate_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
) -> PaginatedResponse[GateEvaluateResponse]:
    items, total = await regression_gate_service.list_gate_evaluations(
        session,
        gate_id=gate_id,
        owner_user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    responses = [_to_evaluate_response(r) for r in items]
    return PaginatedResponse.create(
        items=responses, total=total, page=page, page_size=page_size
    )
