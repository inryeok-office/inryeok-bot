"""Persist the executor execution identity on review jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_jobs",
        sa.Column("execution_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_review_jobs_execution_id",
        "review_jobs",
        ["execution_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_review_jobs_execution_id", table_name="review_jobs")
    op.drop_column("review_jobs", "execution_id")
