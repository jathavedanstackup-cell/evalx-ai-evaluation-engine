"""Create initial EVALX schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_column() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid(), primary_key=True, nullable=False)


def created_at_column() -> sa.Column[object]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "users",
        uuid_column(),
        sa.Column("email", sa.String(length=320), nullable=False),
        created_at_column(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "datasets",
        uuid_column(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        created_at_column(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_datasets_name", "datasets", ["name"])
    op.create_table(
        "dataset_cases",
        uuid_column(),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        created_at_column(),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
    )
    op.create_index("ix_dataset_cases_dataset_id", "dataset_cases", ["dataset_id"])
    op.create_table(
        "evaluations",
        uuid_column(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        created_at_column(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
    )
    op.create_index("ix_evaluations_dataset_id", "evaluations", ["dataset_id"])
    op.create_index("ix_evaluations_name", "evaluations", ["name"])
    op.create_table(
        "evaluator_configs",
        uuid_column(),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_type", sa.String(length=100), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("configuration", postgresql.JSONB(), nullable=True),
        created_at_column(),
        sa.ForeignKeyConstraint(["evaluation_id"], ["evaluations.id"]),
    )
    op.create_index(
        "ix_evaluator_configs_evaluation_id", "evaluator_configs", ["evaluation_id"]
    )
    op.create_index(
        "ix_evaluator_configs_evaluator_type", "evaluator_configs", ["evaluator_type"]
    )
    op.create_table(
        "evaluation_runs",
        uuid_column(),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        created_at_column(),
        sa.ForeignKeyConstraint(["evaluation_id"], ["evaluations.id"]),
    )
    op.create_index(
        "ix_evaluation_runs_evaluation_id", "evaluation_runs", ["evaluation_id"]
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_table(
        "evaluation_results",
        uuid_column(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("factuality_score", sa.Float(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("faithfulness_score", sa.Float(), nullable=True),
        sa.Column("instruction_score", sa.Float(), nullable=True),
        sa.Column("consistency_score", sa.Float(), nullable=True),
        sa.Column("hallucination_score", sa.Float(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        created_at_column(),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["dataset_cases.id"]),
        sa.UniqueConstraint("run_id", "case_id", name="uq_result_run_case"),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])
    op.create_index("ix_evaluation_results_case_id", "evaluation_results", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_results_case_id", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_run_id", table_name="evaluation_results")
    op.drop_table("evaluation_results")
    op.drop_index("ix_evaluation_runs_status", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_evaluation_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluator_configs_evaluator_type", table_name="evaluator_configs")
    op.drop_index("ix_evaluator_configs_evaluation_id", table_name="evaluator_configs")
    op.drop_table("evaluator_configs")
    op.drop_index("ix_evaluations_name", table_name="evaluations")
    op.drop_index("ix_evaluations_dataset_id", table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_index("ix_dataset_cases_dataset_id", table_name="dataset_cases")
    op.drop_table("dataset_cases")
    op.drop_index("ix_datasets_name", table_name="datasets")
    op.drop_table("datasets")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
