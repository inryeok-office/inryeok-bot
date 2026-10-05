"""Widen immutable prompt-version snapshots without truncating history."""

import sqlalchemy as sa
from alembic import context, op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "review_jobs",
        "prompt_version",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    # PostgreSQL would otherwise reject oversized data with an opaque cast
    # failure.  Never silently truncate immutable execution snapshots.
    if context.is_offline_mode() or op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM review_jobs
                    WHERE char_length(prompt_version) > 32
                ) THEN
                    RAISE EXCEPTION
                        'cannot downgrade: prompt_version exceeds 32 characters';
                END IF;
            END $$;
            """
        )
    op.alter_column(
        "review_jobs",
        "prompt_version",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
