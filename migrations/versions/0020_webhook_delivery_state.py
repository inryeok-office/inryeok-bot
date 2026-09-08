"""Track webhook delivery processing without retaining payloads."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column("status", sa.String(length=24), nullable=True, server_default="PROCESSED"),
    )
    op.add_column("webhook_deliveries", sa.Column("safe_reason", sa.String(length=100)))
    op.add_column(
        "webhook_deliveries",
        sa.Column("attempt_count", sa.Integer(), nullable=True, server_default="1"),
    )
    op.add_column("webhook_deliveries", sa.Column("response_status", sa.Integer()))
    op.add_column("webhook_deliveries", sa.Column("correlation_id", sa.String(length=64)))
    op.add_column("webhook_deliveries", sa.Column("installation_id", sa.BigInteger()))
    op.add_column("webhook_deliveries", sa.Column("repository_owner", sa.String(length=255)))
    op.add_column("webhook_deliveries", sa.Column("repository_name", sa.String(length=255)))
    op.add_column(
        "webhook_deliveries",
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column("webhook_deliveries", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.execute(
        sa.text(
            "UPDATE webhook_deliveries SET status = 'PROCESSED', attempt_count = 1 "
            "WHERE status IS NULL"
        )
    )
    op.alter_column("webhook_deliveries", "status", nullable=False, server_default=None)
    op.alter_column("webhook_deliveries", "attempt_count", nullable=False, server_default=None)
    op.create_index(
        "ix_webhook_deliveries_status_created_at",
        "webhook_deliveries",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_status_created_at", table_name="webhook_deliveries")
    for column in (
        "completed_at",
        "processing_started_at",
        "repository_name",
        "repository_owner",
        "installation_id",
        "correlation_id",
        "response_status",
        "attempt_count",
        "safe_reason",
        "status",
    ):
        op.drop_column("webhook_deliveries", column)
