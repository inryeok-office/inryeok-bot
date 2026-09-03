"""add finding categories and check run tracking

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("github_check_run_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "finding_records",
        sa.Column("category", sa.String(32), nullable=False, server_default="BUG"),
    )
    op.alter_column("finding_records", "category", server_default=None)


def downgrade() -> None:
    op.drop_column("finding_records", "category")
    op.drop_column("review_jobs", "github_check_run_id")
