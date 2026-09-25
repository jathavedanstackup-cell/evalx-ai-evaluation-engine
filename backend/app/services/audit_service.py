"""Service layer for durable audit events, tenant querying, and retention pruning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.observability.events import EvaluationEventType
from app.observability.sanitization import (
    sanitize_for_observability,
    validate_execution_metadata,
)
from app.schemas.audit import AuditEventResponse, RunAuditTrailResponse


def to_audit_event_response(event: AuditEvent) -> AuditEventResponse:
    """Serializes an AuditEvent database model into AuditEventResponse."""
    return AuditEventResponse(
        id=event.id,
        event_type=event.event_type,
        timestamp=event.timestamp,
        correlation_id=event.correlation_id,
        actor_user_id=event.actor_user_id,
        owner_user_id=event.owner_user_id,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        run_id=event.run_id,
        outcome=event.outcome,
        duration_ms=event.duration_ms,
        metadata=event.metadata_payload,
        created_at=event.created_at,
    )


async def record_audit_event(
    session: AsyncSession,
    *,
    event_type: str | EvaluationEventType,
    correlation_id: str,
    resource_type: str,
    resource_id: str | UUID | None = None,
    actor_user_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    run_id: UUID | None = None,
    outcome: str = "success",
    duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> AuditEvent:
    """Persists a durable, immutable audit event with sanitized metadata.

    Guarantees no raw prompts, candidate responses, or credentials are saved.
    """
    safe_metadata: dict[str, Any] | None = None
    if metadata is not None:
        sanitized = sanitize_for_observability(metadata, omit_prompts=True)
        safe_metadata = validate_execution_metadata(sanitized)

    et_str = (
        event_type.value
        if isinstance(event_type, EvaluationEventType)
        else str(event_type)
    )

    audit_entry = AuditEvent(
        id=uuid4(),
        event_type=et_str,
        timestamp=timestamp or datetime.now(UTC),
        correlation_id=correlation_id or "evalx-unknown",
        actor_user_id=actor_user_id,
        owner_user_id=owner_user_id,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        run_id=run_id,
        outcome=outcome,
        duration_ms=duration_ms,
        metadata_payload=safe_metadata,
        created_at=datetime.now(UTC),
    )
    session.add(audit_entry)
    return audit_entry


async def list_audit_events(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    run_id: UUID | None = None,
    outcome: str | None = None,
    actor_user_id: UUID | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditEvent], int]:
    """Retrieves a paginated list of audit events for the authenticated tenant."""
    if from_timestamp and to_timestamp and from_timestamp > to_timestamp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_timestamp cannot be greater than to_timestamp",
        )

    # If run_id filter specified, ensure run exists and belongs to owner (404)
    if run_id is not None:
        from app.services.evaluation_run_service import get_evaluation_run

        run = await get_evaluation_run(session, run_id, owner_user_id=owner_user_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Evaluation run not found",
            )

    query = select(AuditEvent).where(AuditEvent.owner_user_id == owner_user_id)

    if event_type:
        query = query.where(AuditEvent.event_type == event_type.lower())
    if resource_type:
        query = query.where(AuditEvent.resource_type == resource_type.lower())
    if resource_id:
        query = query.where(AuditEvent.resource_id == str(resource_id))
    if run_id is not None:
        query = query.where(AuditEvent.run_id == run_id)
    if outcome:
        query = query.where(AuditEvent.outcome == outcome.lower())
    if actor_user_id is not None:
        query = query.where(AuditEvent.actor_user_id == actor_user_id)
    if from_timestamp is not None:
        query = query.where(AuditEvent.timestamp >= from_timestamp)
    if to_timestamp is not None:
        query = query.where(AuditEvent.timestamp <= to_timestamp)

    # Count total matching records
    count_stmt = select(func.count()).select_from(query.subquery())
    total = (await session.scalar(count_stmt)) or 0

    # Deterministic chronological pagination (latest first)
    paged_stmt = (
        query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = (await session.scalars(paged_stmt)).all()
    return list(records), total


async def get_run_audit_trail(
    session: AsyncSession,
    *,
    run_id: UUID,
    owner_user_id: UUID,
) -> RunAuditTrailResponse:
    """Returns a deterministic chronological audit trail for an evaluation run.

    Enforces strict tenant ownership: returns HTTP 404 if the run is not found
    or belongs to another tenant (non-enumerable security).
    """
    from app.services.evaluation_run_service import get_evaluation_run

    run = await get_evaluation_run(session, run_id, owner_user_id=owner_user_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation run not found",
        )

    stmt = (
        select(AuditEvent)
        .where(AuditEvent.run_id == run_id)
        .order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc())
    )
    events = (await session.scalars(stmt)).all()
    event_responses = [to_audit_event_response(e) for e in events]

    return RunAuditTrailResponse(
        run_id=run_id,
        total_events=len(event_responses),
        events=event_responses,
    )


async def prune_audit_events(
    session: AsyncSession,
    *,
    older_than_days: int,
    preserve_run_audits: bool = True,
    batch_limit: int = 1000,
) -> int:
    """Bounded administrative retention cleanup.

    Deletes audit events older than specified days. When preserve_run_audits
    is True, preserves evaluation run lifecycle events required for reproducibility.
    """
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")

    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)

    # Find IDs to delete in bounded batch
    query = (
        select(AuditEvent.id).where(AuditEvent.timestamp < cutoff).limit(batch_limit)
    )
    if preserve_run_audits:
        query = query.where(AuditEvent.run_id.is_(None))

    ids_to_delete = (await session.scalars(query)).all()
    if not ids_to_delete:
        return 0

    del_stmt = delete(AuditEvent).where(AuditEvent.id.in_(ids_to_delete))
    await session.execute(del_stmt)
    await session.commit()
    return len(ids_to_delete)
