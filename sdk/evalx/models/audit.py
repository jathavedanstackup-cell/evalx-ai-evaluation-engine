"""Audit and metrics models for the EVALX SDK."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from evalx.models.common import EvalXBaseModel


class AuditEvent(EvalXBaseModel):
    """Immutable audit event recording a system action or state transition."""

    id: UUID
    event_type: str
    resource_type: str
    resource_id: str | None = None
    outcome: str = "success"
    actor_user_id: UUID | None = None
    owner_user_id: UUID | None = None
    run_id: UUID | None = None
    correlation_id: str | None = None
    ip_address: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] | None = None
    timestamp: datetime
    created_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_audit_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "event_type" not in data and "action" in data:
                data["event_type"] = data["action"]
            if "metadata" not in data and "details" in data:
                data["metadata"] = data["details"]
        return data

    @property
    def action(self) -> str:
        return self.event_type

    @property
    def user_id(self) -> UUID | None:
        return self.actor_user_id or self.owner_user_id

    @property
    def details(self) -> dict[str, Any]:
        return self.metadata or {}


class MetricsSnapshot(EvalXBaseModel):
    """Snapshot of real-time server operational metrics."""

    api: dict[str, Any] = Field(default_factory=dict)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    queue: dict[str, Any] = Field(default_factory=dict)
    security: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)
    total_runs_started: int = 0
    total_runs_completed: int = 0
    total_runs_failed: int = 0
    total_evaluations: int = 0
    rate_limit_hits: int = 0
    rate_limiter_degraded_events: int = 0
    request_body_rejections: int = 0
    snapshot_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
