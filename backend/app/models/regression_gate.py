from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation_configuration import EvaluationConfig
    from app.models.evaluation_run import EvaluationRun
    from app.models.user import User


class RegressionGate(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """Reusable quality/regression gate defining rule criteria for evaluation runs."""

    __tablename__ = "regression_gates"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "name", name="uq_regression_gates_owner_name"
        ),
    )

    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    configuration: Mapped[EvaluationConfig | None] = relationship(
        foreign_keys=[configuration_id]
    )
    versions: Mapped[list[RegressionGateVersion]] = relationship(
        back_populates="gate",
        cascade="all, delete-orphan",
        order_by="RegressionGateVersion.version",
    )


class RegressionGateVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable historical version of a regression gate."""

    __tablename__ = "regression_gate_versions"
    __table_args__ = (
        UniqueConstraint(
            "gate_id", "version", name="uq_regression_gate_versions_gate_ver"
        ),
    )

    gate_id: Mapped[UUID] = mapped_column(
        ForeignKey("regression_gates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration_id: Mapped[UUID | None] = mapped_column(nullable=True)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )

    gate: Mapped[RegressionGate] = relationship(back_populates="versions")


class RegressionGateResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Persisted deterministic evaluation outcome of a regression gate against a run."""

    __tablename__ = "regression_gate_evaluations"

    gate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("regression_gates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    gate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    baseline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    gate_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    overall_status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    target_run: Mapped[EvaluationRun] = relationship(foreign_keys=[target_run_id])
    baseline_run: Mapped[EvaluationRun | None] = relationship(
        foreign_keys=[baseline_run_id]
    )
