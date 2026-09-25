"""Add regression_gates, regression_gate_versions, evaluations, and experiments.

Revision ID: 0009_regression_gates
Revises: 0008_evaluation_configs
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_regression_gates"
down_revision: str | Sequence[str] | None = "0008_evaluation_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create regression_gates table
    op.create_table(
        "regression_gates",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("configuration_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "rules",
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
            name="fk_regression_gates_owner_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["evaluation_configs.id"],
            name="fk_regression_gates_configuration_id_configs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "owner_user_id", "name", name="uq_regression_gates_owner_name"
        ),
    )
    op.create_index(
        "ix_regression_gates_owner_user_id",
        "regression_gates",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_regression_gates_name",
        "regression_gates",
        ["name"],
    )
    op.create_index(
        "ix_regression_gates_configuration_id",
        "regression_gates",
        ["configuration_id"],
    )

    # 2. Create regression_gate_versions table
    op.create_table(
        "regression_gate_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("gate_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("configuration_id", sa.Uuid(), nullable=True),
        sa.Column(
            "rules",
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
            ["gate_id"],
            ["regression_gates.id"],
            name="fk_regression_gate_versions_gate_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "gate_id", "version", name="uq_regression_gate_versions_gate_ver"
        ),
    )
    op.create_index(
        "ix_regression_gate_versions_gate_id",
        "regression_gate_versions",
        ["gate_id"],
    )

    # 3. Create regression_gate_evaluations table
    op.create_table(
        "regression_gate_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("gate_id", sa.Uuid(), nullable=True),
        sa.Column("gate_version", sa.Integer(), nullable=False),
        sa.Column("target_run_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "gate_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_hash",
            sa.String(length=64),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("overall_status", sa.String(length=50), nullable=False),
        sa.Column(
            "rule_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["gate_id"],
            ["regression_gates.id"],
            name="fk_regression_gate_evaluations_gate_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_run_id"],
            ["evaluation_runs.id"],
            name="fk_regression_gate_evaluations_target_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_run_id"],
            ["evaluation_runs.id"],
            name="fk_regression_gate_evaluations_baseline_run_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_regression_gate_evaluations_gate_id",
        "regression_gate_evaluations",
        ["gate_id"],
    )
    op.create_index(
        "ix_regression_gate_evaluations_target_run_id",
        "regression_gate_evaluations",
        ["target_run_id"],
    )
    op.create_index(
        "ix_regression_gate_evaluations_baseline_run_id",
        "regression_gate_evaluations",
        ["baseline_run_id"],
    )
    op.create_index(
        "ix_regression_gate_evaluations_overall_status",
        "regression_gate_evaluations",
        ["overall_status"],
    )
    op.create_index(
        "ix_regression_gate_evaluations_correlation_id",
        "regression_gate_evaluations",
        ["correlation_id"],
    )

    # 4. Create experiments table
    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("configuration_id", sa.Uuid(), nullable=True),
        sa.Column("baseline_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default=sa.text("'active'"),
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
            name="fk_experiments_owner_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["evaluation_configs.id"],
            name="fk_experiments_configuration_id_configs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_run_id"],
            ["evaluation_runs.id"],
            name="fk_experiments_baseline_run_id_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_experiments_owner_name"),
    )
    op.create_index("ix_experiments_owner_user_id", "experiments", ["owner_user_id"])
    op.create_index("ix_experiments_name", "experiments", ["name"])
    op.create_index(
        "ix_experiments_configuration_id", "experiments", ["configuration_id"]
    )
    op.create_index(
        "ix_experiments_baseline_run_id", "experiments", ["baseline_run_id"]
    )


def downgrade() -> None:
    op.drop_table("experiments")
    op.drop_table("regression_gate_evaluations")
    op.drop_table("regression_gate_versions")
    op.drop_table("regression_gates")
