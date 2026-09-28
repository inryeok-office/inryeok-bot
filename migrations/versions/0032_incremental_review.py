"""Preserve synchronize provenance without changing historical jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("trigger_action", sa.String(32), nullable=True))
    op.add_column("review_jobs", sa.Column("previous_reviewed_head", sa.String(64), nullable=True))
    op.add_column("review_jobs", sa.Column("incremental_diff_bytes", sa.Integer(), nullable=True))
    op.add_column("review_runs", sa.Column("duplicate_only", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_runs", "duplicate_only")
    op.drop_column("review_jobs", "incremental_diff_bytes")
    op.drop_column("review_jobs", "previous_reviewed_head")
    op.drop_column("review_jobs", "trigger_action")
