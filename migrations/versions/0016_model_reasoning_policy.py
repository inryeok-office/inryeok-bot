"""Add model reasoning effort to review policy and job snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "global_review_settings",
        sa.Column("reasoning_effort", sa.String(length=16), server_default="medium", nullable=False),
    )
    op.add_column("repository_settings", sa.Column("override_reasoning_effort", sa.String(length=16)))
    op.add_column("review_jobs", sa.Column("model", sa.String(length=128)))
    op.add_column("review_jobs", sa.Column("reasoning_effort", sa.String(length=16)))


def downgrade() -> None:
    op.drop_column("review_jobs", "reasoning_effort")
    op.drop_column("review_jobs", "model")
    op.drop_column("repository_settings", "override_reasoning_effort")
    op.drop_column("global_review_settings", "reasoning_effort")
