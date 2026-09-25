"""Add run_analyses table for failure analysis and insights.

Revision ID: 0010_run_analysis
Revises: 0009_regression_gates
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_run_analysis"
down_revision: str | Sequence[str] | None = "0009_regression_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "snapshot_hash",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "analysis_data",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_run_analyses_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_run_id"],
            ["evaluation_runs.id"],
            name="fk_run_analyses_baseline_run_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_run_analyses_run_id",
        "run_analyses",
        ["run_id"],
    )
    op.create_index(
        "ix_run_analyses_baseline_run_id",
        "run_analyses",
        ["baseline_run_id"],
    )
    op.create_index(
        "ix_run_analyses_run_baseline",
        "run_analyses",
        ["run_id", "baseline_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_analyses_run_baseline", table_name="run_analyses")
    op.drop_index("ix_run_analyses_baseline_run_id", table_name="run_analyses")
    op.drop_index("ix_run_analyses_run_id", table_name="run_analyses")
    op.drop_table("run_analyses")
