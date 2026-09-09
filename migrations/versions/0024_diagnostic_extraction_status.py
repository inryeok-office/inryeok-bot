"""Record safe diagnostic fallback usage on review jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_jobs",
        sa.Column("diagnostic_extraction_failed", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_jobs", "diagnostic_extraction_failed")
