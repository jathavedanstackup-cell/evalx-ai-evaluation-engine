"""Add audit_events table for production auditability and observability.

Revision ID: 0011_add_audit_events
Revises: 0010_run_analysis
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_add_audit_events"
down_revision: str | Sequence[str] | None = "0010_run_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "outcome",
            sa.String(length=32),
            nullable=False,
            server_default="success",
        ),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("metadata_payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_events_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_audit_events_owner_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_audit_events_run_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_audit_events_event_type",
        "audit_events",
        ["event_type"],
    )
    op.create_index(
        "ix_audit_events_timestamp",
        "audit_events",
        ["timestamp"],
    )
    op.create_index(
        "ix_audit_events_correlation_id",
        "audit_events",
        ["correlation_id"],
    )
    op.create_index(
        "ix_audit_events_actor_user_id",
        "audit_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_audit_events_owner_user_id",
        "audit_events",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_audit_events_resource_type",
        "audit_events",
        ["resource_type"],
    )
    op.create_index(
        "ix_audit_events_resource_id",
        "audit_events",
        ["resource_id"],
    )
    op.create_index(
        "ix_audit_events_run_id",
        "audit_events",
        ["run_id"],
    )
    op.create_index(
        "ix_audit_events_outcome",
        "audit_events",
        ["outcome"],
    )
    op.create_index(
        "ix_audit_events_owner_timestamp",
        "audit_events",
        ["owner_user_id", "timestamp"],
    )
    op.create_index(
        "ix_audit_events_run_timestamp",
        "audit_events",
        ["run_id", "timestamp"],
    )
    op.create_index(
        "ix_audit_events_resource",
        "audit_events",
        ["resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource", table_name="audit_events")
    op.drop_index("ix_audit_events_run_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_owner_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_outcome", table_name="audit_events")
    op.drop_index("ix_audit_events_run_id", table_name="audit_events")
    op.drop_index("ix_audit_events_resource_id", table_name="audit_events")
    op.drop_index("ix_audit_events_resource_type", table_name="audit_events")
    op.drop_index("ix_audit_events_owner_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_table("audit_events")
