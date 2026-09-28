"""Nullable metadata; historical executions are intentionally unknown."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("context_manifest", sa.JSON(), nullable=True))
    op.add_column("review_runs", sa.Column("stage_counts", sa.JSON(), nullable=True))
    op.add_column("review_runs", sa.Column("publisher_fallback", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_runs", "publisher_fallback")
    op.drop_column("review_runs", "stage_counts")
    op.drop_column("review_jobs", "context_manifest")
