"""Add immutable review locale presentation snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("effective_locale", sa.String(length=8), nullable=True))
    op.add_column("review_jobs", sa.Column("locale_source", sa.String(length=32), nullable=True))
    op.add_column(
        "review_jobs", sa.Column("message_catalog_version", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("review_jobs", "message_catalog_version")
    op.drop_column("review_jobs", "locale_source")
    op.drop_column("review_jobs", "effective_locale")
