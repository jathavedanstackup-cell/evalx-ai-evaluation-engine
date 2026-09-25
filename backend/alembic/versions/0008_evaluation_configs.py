"""Add evaluation_configs and evaluation_config_versions tables.

Also adds run snapshot columns.

Revision ID: 0008_evaluation_configs
Revises: 0007_dataset_owner_set_null
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_evaluation_configs"
down_revision: str | Sequence[str] | None = "0007_dataset_owner_set_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create evaluation_configs table
    op.create_table(
        "evaluation_configs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "evaluators",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_hash",
            sa.String(length=64),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_evaluation_configs_owner_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_evaluation_configs_owner_user_id",
        "evaluation_configs",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_evaluation_configs_name",
        "evaluation_configs",
        ["name"],
    )

    # 2. Create evaluation_config_versions table
    op.create_table(
        "evaluation_config_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("config_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "evaluators",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_hash",
            sa.String(length=64),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["evaluation_configs.id"],
            name="fk_evaluation_config_versions_config_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "config_id", "version", name="uq_evaluation_config_version"
        ),
    )
    op.create_index(
        "ix_evaluation_config_versions_config_id",
        "evaluation_config_versions",
        ["config_id"],
    )

    # 3. Add configuration snapshot columns to evaluation_runs
    op.add_column("evaluation_runs", sa.Column("config_id", sa.Uuid(), nullable=True))
    op.add_column(
        "evaluation_runs", sa.Column("config_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("config_snapshot_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_evaluation_runs_config_id_evaluation_configs",
        "evaluation_runs",
        "evaluation_configs",
        ["config_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_evaluation_runs_config_id", "evaluation_runs", ["config_id"])


def downgrade() -> None:
    op.drop_constraint(
        "fk_evaluation_runs_config_id_evaluation_configs",
        "evaluation_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_evaluation_runs_config_id", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "config_snapshot")
    op.drop_column("evaluation_runs", "config_snapshot_hash")
    op.drop_column("evaluation_runs", "config_version")
    op.drop_column("evaluation_runs", "config_id")

    op.drop_table("evaluation_config_versions")
    op.drop_table("evaluation_configs")
