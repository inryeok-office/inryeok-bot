"""confirmed installation, command, and admin policy

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repository_settings",
        sa.Column("installed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "repository_settings",
        sa.Column("ignore_draft", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("repository_settings", "installed", server_default=None)
    op.alter_column("repository_settings", "ignore_draft", server_default=None)
    op.add_column("review_jobs", sa.Column("source_comment_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_review_jobs_source_comment_id", "review_jobs", ["source_comment_id"]
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.String(100), nullable=False, unique=True),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(255), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("admin_sessions")
    op.drop_table("webhook_deliveries")
    op.drop_constraint("uq_review_jobs_source_comment_id", "review_jobs", type_="unique")
    op.drop_column("review_jobs", "source_comment_id")
    op.drop_column("repository_settings", "ignore_draft")
    op.drop_column("repository_settings", "installed")
