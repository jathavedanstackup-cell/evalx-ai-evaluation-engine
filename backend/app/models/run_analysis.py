from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation_run import EvaluationRun


class RunAnalysis(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Persisted deterministic failure analysis outcome for an evaluation run."""

    __tablename__ = "run_analyses"
    __table_args__ = (
        Index("ix_run_analyses_run_baseline", "run_id", "baseline_run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    baseline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    analysis_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[EvaluationRun] = relationship(foreign_keys=[run_id])
    baseline_run: Mapped[EvaluationRun | None] = relationship(
        foreign_keys=[baseline_run_id]
    )
