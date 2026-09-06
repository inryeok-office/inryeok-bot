"""Add review scheduling and command cooldown settings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("not_before", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_review_jobs_not_before", "review_jobs", ["not_before"])
    op.add_column(
        "global_review_settings",
        sa.Column("synchronize_debounce_seconds", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column(
        "global_review_settings",
        sa.Column("command_cooldown_seconds", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column(
        "repository_settings", sa.Column("override_synchronize_debounce_seconds", sa.Integer())
    )
    op.add_column(
        "repository_settings", sa.Column("override_command_cooldown_seconds", sa.Integer())
    )
    op.execute("UPDATE global_review_settings SET review_on_synchronize = false")


def downgrade() -> None:
    op.drop_column("repository_settings", "override_command_cooldown_seconds")
    op.drop_column("repository_settings", "override_synchronize_debounce_seconds")
    op.drop_column("global_review_settings", "command_cooldown_seconds")
    op.drop_column("global_review_settings", "synchronize_debounce_seconds")
    op.drop_index("ix_review_jobs_not_before", table_name="review_jobs")
    op.drop_column("review_jobs", "not_before")
