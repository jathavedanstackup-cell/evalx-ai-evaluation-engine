from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation import Evaluation
    from app.models.evaluation_configuration import EvaluationConfig
    from app.models.evaluation_result import EvaluationResult


class EvaluationRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluation_runs"

    evaluation_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluations.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(50), index=True)
    dataset_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    dataset_snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overall_score: Mapped[float | None] = mapped_column(Float)
    total_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    metrics_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    execution_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    candidate_responses: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    evaluation: Mapped[Evaluation] = relationship(back_populates="runs")
    configuration: Mapped[EvaluationConfig | None] = relationship(
        foreign_keys=[config_id]
    )

    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
