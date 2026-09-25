"""Change dataset owner_user_id FK ondelete to SET NULL.

Revision ID: 0007_dataset_owner_set_null
Revises: 0006_user_auth_dataset_owner
Create Date: 2026-09-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_dataset_owner_set_null"
down_revision: str | Sequence[str] | None = "0006_user_auth_dataset_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_datasets_owner_user_id_users", "datasets", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_datasets_owner_user_id_users",
        "datasets",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_datasets_owner_user_id_users", "datasets", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_datasets_owner_user_id_users",
        "datasets",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
