from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation import Evaluation
    from app.models.evaluation_result import EvaluationResult
    from app.models.user import User


class Dataset(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "datasets"

    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )

    owner: Mapped[User | None] = relationship(back_populates="datasets")
    cases: Mapped[list[DatasetCase]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list[Evaluation]] = relationship(back_populates="dataset")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("version", 1)
        super().__init__(*args, **kwargs)


class DatasetCase(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "dataset_cases"

    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text)
    context: Mapped[list[str] | dict[str, Any] | None] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    dataset: Mapped[Dataset] = relationship(back_populates="cases")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="case")
