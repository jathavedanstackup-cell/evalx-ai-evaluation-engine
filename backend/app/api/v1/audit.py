"""API endpoints for audit event retrieval and operational metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_async_session
from app.models.user import User
from app.observability.correlation import validate_and_normalize_correlation_id
from app.observability.metrics import default_metrics_collector
from app.schemas.audit import AuditEventResponse, MetricsSnapshotResponse
from app.schemas.common import PaginatedResponse
from app.security.rate_limiter import rate_limit_standard
from app.services import audit_service

router = APIRouter(tags=["Audit & Observability"])


@router.get(
    "/audit-events",
    response_model=PaginatedResponse[AuditEventResponse],
    status_code=status.HTTP_200_OK,
    summary="List tenant audit events with bounded filtering and pagination",
    dependencies=[Depends(rate_limit_standard)],
)
async def list_audit_events(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    event_type: Annotated[
        str | None,
        Query(max_length=64, description="Filter by event type"),
    ] = None,
    resource_type: Annotated[
        str | None,
        Query(max_length=64, description="Filter by resource type"),
    ] = None,
    resource_id: Annotated[
        str | None,
        Query(max_length=100, description="Filter by resource ID"),
    ] = None,
    run_id: Annotated[
        UUID | None,
        Query(description="Filter by evaluation run UUID"),
    ] = None,
    outcome: Annotated[
        str | None,
        Query(max_length=32, description="Filter by outcome (e.g. success, failure)"),
    ] = None,
    actor_user_id: Annotated[
        UUID | None,
        Query(description="Filter by actor user UUID"),
    ] = None,
    from_timestamp: Annotated[
        datetime | None,
        Query(description="Start timestamp (inclusive ISO 8601)"),
    ] = None,
    to_timestamp: Annotated[
        datetime | None,
        Query(description="End timestamp (inclusive ISO 8601)"),
    ] = None,
    page: Annotated[
        int,
        Query(ge=1, le=10_000, description="Page number (1-10000)"),
    ] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, description="Items per page (1-100)"),
    ] = 20,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> PaginatedResponse[AuditEventResponse]:
    """Retrieves an immutable, tenant-scoped audit log.

    Enforces strict tenant isolation: users can only view audit logs associated
    with their own resources.
    """
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    events, total = await audit_service.list_audit_events(
        session=session,
        owner_user_id=current_user.id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        run_id=run_id,
        outcome=outcome,
        actor_user_id=actor_user_id,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        page=page,
        page_size=page_size,
    )

    items = [audit_service.to_audit_event_response(e) for e in events]
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/metrics",
    response_model=MetricsSnapshotResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve application operational metrics snapshot",
    dependencies=[Depends(rate_limit_standard)],
)
async def get_metrics(
    response: Response,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> MetricsSnapshotResponse:
    """Returns a bounded operational metrics snapshot for production diagnosis."""
    valid_cid = validate_and_normalize_correlation_id(x_correlation_id)
    response.headers["X-Correlation-ID"] = valid_cid

    snapshot = default_metrics_collector.get_snapshot()
    return MetricsSnapshotResponse(**snapshot)
