"""Add operational metadata and correlation tracking to evaluation runs.

Revision ID: 0004_add_run_observability
Revises: 0003_add_run_and_result_fields
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_add_run_observability"
down_revision: str | Sequence[str] | None = "0003_add_run_and_result_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column("duration_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_evaluation_runs_correlation_id",
        "evaluation_runs",
        ["correlation_id"],
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "execution_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("evaluation_runs", "execution_metadata")
    op.drop_index("ix_evaluation_runs_correlation_id", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "correlation_id")
    op.drop_column("evaluation_runs", "duration_ms")
