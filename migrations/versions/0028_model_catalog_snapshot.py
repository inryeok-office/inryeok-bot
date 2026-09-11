"""Add model catalog identity to immutable review job snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_jobs",
        sa.Column("model_catalog_entry_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "review_jobs",
        sa.Column("model_verification_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_jobs", "model_verification_id")
    op.drop_column("review_jobs", "model_catalog_entry_id")
