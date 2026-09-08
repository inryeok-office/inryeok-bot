"""Store safe per-stage finding rejection counts."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_runs", sa.Column("rejection_counts", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_runs", "rejection_counts")
