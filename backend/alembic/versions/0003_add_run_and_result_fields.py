"""Add run execution and case result fields.

Revision ID: 0003_add_run_and_result_fields
Revises: 0002_add_dataset_version
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_add_run_and_result_fields"
down_revision: str | Sequence[str] | None = "0002_add_dataset_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # evaluation_runs additions
    op.add_column(
        "evaluation_runs",
        sa.Column("dataset_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "dataset_snapshot_hash",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("total_cases", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("completed_cases", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("failed_cases", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "metrics_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # evaluation_results additions
    op.add_column(
        "evaluation_results",
        sa.Column("passed", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("execution_time_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evaluation_results", "error_message")
    op.drop_column("evaluation_results", "metrics")
    op.drop_column("evaluation_results", "execution_time_ms")
    op.drop_column("evaluation_results", "status")
    op.drop_column("evaluation_results", "passed")

    op.drop_column("evaluation_runs", "metrics_summary")
    op.drop_column("evaluation_runs", "error_message")
    op.drop_column("evaluation_runs", "failed_cases")
    op.drop_column("evaluation_runs", "completed_cases")
    op.drop_column("evaluation_runs", "total_cases")
    op.drop_column("evaluation_runs", "dataset_snapshot_hash")
    op.drop_column("evaluation_runs", "dataset_version")
