"""Pydantic schemas for audit events and observability metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEventResponse(BaseModel):
    """Immutable audit record representation returned by API."""

    id: UUID
    event_type: str
    timestamp: datetime
    correlation_id: str
    actor_user_id: UUID | None = None
    owner_user_id: UUID | None = None
    resource_type: str
    resource_id: str | None = None
    run_id: UUID | None = None
    outcome: str
    duration_ms: float | None = None
    metadata: dict[str, Any] | None = Field(default=None, alias="metadata")
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )


class RunAuditTrailResponse(BaseModel):
    """Chronological audit trail for an evaluation run."""

    run_id: UUID
    total_events: int
    events: list[AuditEventResponse]

    model_config = ConfigDict(from_attributes=True)


class MetricsSnapshotResponse(BaseModel):
    """Production operational metrics diagnostic snapshot."""

    api: dict[str, Any]
    evaluation: dict[str, Any]
    queue: dict[str, Any]
    security: dict[str, Any]
    schedule: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)
