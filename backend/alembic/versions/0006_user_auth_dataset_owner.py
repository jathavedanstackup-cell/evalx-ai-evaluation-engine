"""Add user external_auth_id and dataset owner_user_id.

Revision ID: 0006_user_auth_dataset_owner
Revises: 0005_add_candidate_responses
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_user_auth_dataset_owner"
down_revision: str | Sequence[str] | None = "0005_add_candidate_responses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update users table: external_auth_id and nullable email
    op.add_column(
        "users",
        sa.Column("external_auth_id", sa.String(length=255), nullable=False),
    )
    op.create_index(
        "ix_users_external_auth_id",
        "users",
        ["external_auth_id"],
        unique=True,
    )
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=True,
    )

    # 2. Update datasets table: owner_user_id foreign key to users
    op.add_column(
        "datasets",
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_datasets_owner_user_id_users",
        "datasets",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_datasets_owner_user_id",
        "datasets",
        ["owner_user_id"],
    )


def downgrade() -> None:
    # 1. Revert datasets table
    op.drop_index("ix_datasets_owner_user_id", table_name="datasets")
    op.drop_constraint(
        "fk_datasets_owner_user_id_users", "datasets", type_="foreignkey"
    )
    op.drop_column("datasets", "owner_user_id")

    # 2. Revert users table
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.drop_index("ix_users_external_auth_id", table_name="users")
    op.drop_column("users", "external_auth_id")
