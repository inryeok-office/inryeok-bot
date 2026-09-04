"""align failure notice timestamp nullability

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "review_failure_notices",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "review_failure_notices",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
