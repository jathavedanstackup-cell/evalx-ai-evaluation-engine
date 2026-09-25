from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation import Evaluation


class EvaluatorConfig(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluator_configs"

    evaluation_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluations.id"), index=True
    )
    evaluator_type: Mapped[str] = mapped_column(String(100), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evaluation: Mapped[Evaluation] = relationship(back_populates="evaluator_configs")
