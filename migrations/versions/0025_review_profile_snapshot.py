"""Store the effective review detail on each job."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("review_profile", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("review_jobs", "review_profile")
