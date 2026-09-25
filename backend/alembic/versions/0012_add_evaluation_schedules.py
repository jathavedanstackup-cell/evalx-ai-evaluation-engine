"""Add evaluation_schedules and schedule_executions tables for Step 15.

Revision ID: 0012_add_evaluation_schedules
Revises: 0011_add_audit_events
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_add_evaluation_schedules"
down_revision: str | Sequence[str] | None = "0011_add_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column("schedule_type", sa.String(length=32), nullable=False),
        sa.Column(
            "schedule_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_id", sa.Uuid(), nullable=True),
        sa.Column("configuration_version", sa.Integer(), nullable=True),
        sa.Column("baseline_run_id", sa.Uuid(), nullable=True),
        sa.Column("gate_id", sa.Uuid(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "last_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
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
            name="fk_evaluation_schedules_owner_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_evaluation_schedules_dataset_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["evaluation_configs.id"],
            name="fk_evaluation_schedules_configuration_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_run_id"],
            ["evaluation_runs.id"],
            name="fk_evaluation_schedules_baseline_run_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["gate_id"],
            ["regression_gates.id"],
            name="fk_evaluation_schedules_gate_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id"],
            ["evaluation_runs.id"],
            name="fk_evaluation_schedules_last_run_id",
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_evaluation_schedules_owner_user_id",
        "evaluation_schedules",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_evaluation_schedules_name",
        "evaluation_schedules",
        ["name"],
    )
    op.create_index(
        "ix_evaluation_schedules_enabled",
        "evaluation_schedules",
        ["enabled"],
    )
    op.create_index(
        "ix_evaluation_schedules_schedule_type",
        "evaluation_schedules",
        ["schedule_type"],
    )
    op.create_index(
        "ix_evaluation_schedules_dataset_id",
        "evaluation_schedules",
        ["dataset_id"],
    )
    op.create_index(
        "ix_evaluation_schedules_configuration_id",
        "evaluation_schedules",
        ["configuration_id"],
    )
    op.create_index(
        "ix_evaluation_schedules_next_run_at",
        "evaluation_schedules",
        ["next_run_at"],
    )
    op.create_index(
        "ix_evaluation_schedules_due",
        "evaluation_schedules",
        ["enabled", "next_run_at"],
    )

    op.create_table(
        "schedule_executions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_status",
            sa.String(length=32),
            nullable=False,
            server_default="triggered",
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["evaluation_schedules.id"],
            name="fk_schedule_executions_schedule_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_schedule_executions_run_id",
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_schedule_executions_schedule_id",
        "schedule_executions",
        ["schedule_id"],
    )
    op.create_index(
        "ix_schedule_executions_run_id",
        "schedule_executions",
        ["run_id"],
    )
    op.create_index(
        "ix_schedule_executions_correlation_id",
        "schedule_executions",
        ["correlation_id"],
    )
    op.create_index(
        "ix_schedule_executions_history",
        "schedule_executions",
        ["schedule_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("schedule_executions")
    op.drop_table("evaluation_schedules")
