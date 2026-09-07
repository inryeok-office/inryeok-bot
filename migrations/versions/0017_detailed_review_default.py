"""Use detailed review as the new safe default for the global policy."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The pre-V1.1 installation default was BALANCED.  Preserve repository
    # overrides; only the singleton global default is promoted.
    op.execute(
        sa.text(
            "UPDATE global_review_settings SET review_profile = 'THOROUGH' "
            "WHERE id = 1 AND review_profile = 'BALANCED'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE global_review_settings SET review_profile = 'BALANCED' "
            "WHERE id = 1 AND review_profile = 'THOROUGH'"
        )
    )
