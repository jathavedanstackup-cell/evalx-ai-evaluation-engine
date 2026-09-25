from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.common import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class EvaluationConfig(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """Reusable evaluation preset defining evaluators, weights, thresholds."""

    __tablename__ = "evaluation_configs"

    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    evaluators: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    versions: Mapped[list[EvaluationConfigVersion]] = relationship(
        back_populates="config",
        cascade="all, delete-orphan",
        order_by="EvaluationConfigVersion.version",
    )


class EvaluationConfigVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Historical immutable snapshot version of an evaluation configuration."""

    __tablename__ = "evaluation_config_versions"
    __table_args__ = (
        UniqueConstraint("config_id", "version", name="uq_evaluation_config_version"),
    )

    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluators: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )

    config: Mapped[EvaluationConfig] = relationship(back_populates="versions")
