"""add deduplicated review failure notices

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_failure_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_owner", sa.String(length=255), nullable=False),
        sa.Column("repository_name", sa.String(length=255), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("error_category", sa.String(length=32), nullable=False),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "repository_owner",
            "repository_name",
            "pull_request_number",
            "head_sha",
            "error_category",
            name="uq_review_failure_notice",
        ),
    )


def downgrade() -> None:
    op.drop_table("review_failure_notices")
