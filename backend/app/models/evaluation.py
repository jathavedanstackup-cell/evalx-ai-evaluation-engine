from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import UpdatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.dataset import Dataset
    from app.models.evaluation_run import EvaluationRun
    from app.models.evaluator_config import EvaluatorConfig


class Evaluation(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "evaluations"

    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    model_provider: Mapped[str] = mapped_column(String(100))
    model_name: Mapped[str] = mapped_column(String(255))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    dataset: Mapped[Dataset] = relationship(back_populates="evaluations")
    evaluator_configs: Mapped[list[EvaluatorConfig]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )
    runs: Mapped[list[EvaluationRun]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )
