from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import UpdatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.evaluation_configuration import EvaluationConfig
    from app.models.evaluation_run import EvaluationRun
    from app.models.user import User


class Experiment(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """Experiment grouping comparable evaluation runs and gate decisions."""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_experiments_owner_name"),
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
    baseline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", server_default="active"
    )

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    configuration: Mapped[EvaluationConfig | None] = relationship(
        foreign_keys=[configuration_id]
    )
    baseline_run: Mapped[EvaluationRun | None] = relationship(
        foreign_keys=[baseline_run_id]
    )
