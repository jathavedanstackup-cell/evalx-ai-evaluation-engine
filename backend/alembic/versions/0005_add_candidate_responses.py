"""Add candidate responses to evaluation runs.

Revision ID: 0005_add_candidate_responses
Revises: 0004_add_run_observability
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_add_candidate_responses"
down_revision: str | Sequence[str] | None = "0004_add_run_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "candidate_responses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("evaluation_runs", "candidate_responses")
