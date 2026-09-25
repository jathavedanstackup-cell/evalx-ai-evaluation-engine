from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation_run import EvaluationRun
    from app.models.user import User


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Durable append-only audit event capturing system and user actions.

    Maintains immutable historical records with safe deletion semantics
    (ondelete="SET NULL") ensuring that user or evaluation run deletion never
    destroys historical audit trails.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_owner_timestamp", "owner_user_id", "timestamp"),
        Index("ix_audit_events_run_timestamp", "run_id", "timestamp"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False, default="success", index=True
    )
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    actor_user: Mapped[User | None] = relationship("User", foreign_keys=[actor_user_id])
    owner_user: Mapped[User | None] = relationship("User", foreign_keys=[owner_user_id])
    run: Mapped[EvaluationRun | None] = relationship(
        "EvaluationRun", foreign_keys=[run_id]
    )
