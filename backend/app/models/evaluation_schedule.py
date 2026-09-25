"""SQLAlchemy persistence models for Step 15: Evaluation Automation & Scheduling.

Defines EvaluationSchedule and ScheduleExecution entities with strict
cascading, indexing, and historical run preservation semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.dataset import Dataset
    from app.models.evaluation_configuration import EvaluationConfig
    from app.models.evaluation_run import EvaluationRun
    from app.models.regression_gate import RegressionGate
    from app.models.user import User


class EvaluationSchedule(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """First-class schedule resource for recurring or automated evaluations."""

    __tablename__ = "evaluation_schedules"
    __table_args__ = (Index("ix_evaluation_schedules_due", "enabled", "next_run_at"),)

    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    schedule_definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    configuration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    configuration_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    gate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("regression_gates.id", ondelete="SET NULL"),
        nullable=True,
    )

    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    # Relationships
    owner: Mapped[User] = relationship()
    dataset: Mapped[Dataset] = relationship()
    configuration: Mapped[EvaluationConfig | None] = relationship()
    baseline_run: Mapped[EvaluationRun | None] = relationship(
        foreign_keys=[baseline_run_id]
    )
    last_run: Mapped[EvaluationRun | None] = relationship(foreign_keys=[last_run_id])
    gate: Mapped[RegressionGate | None] = relationship()
    executions: Mapped[list[ScheduleExecution]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="desc(ScheduleExecution.created_at)",
    )


class ScheduleExecution(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Bounded execution history record for a schedule execution."""

    __tablename__ = "schedule_executions"
    __table_args__ = (
        Index("ix_schedule_executions_history", "schedule_id", "created_at"),
    )

    schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    execution_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="triggered",
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    # Relationships
    schedule: Mapped[EvaluationSchedule] = relationship(back_populates="executions")
    run: Mapped[EvaluationRun | None] = relationship()
