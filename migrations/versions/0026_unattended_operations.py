"""Add safe lifecycle snapshots and operational incidents."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("model_source", sa.String(length=32)),
        ("reasoning_source", sa.String(length=32)),
        ("model_catalog_version", sa.String(length=64)),
        ("schema_hash", sa.String(length=64)),
        ("codex_cli_version", sa.String(length=64)),
        ("executor_runtime_version", sa.String(length=64)),
        ("terminal_outcome", sa.String(length=64)),
        ("reaction_cleanup_status", sa.String(length=32)),
    ):
        op.add_column("review_jobs", sa.Column(name, column, nullable=True))
    op.create_index("ix_review_jobs_terminal_outcome", "review_jobs", ["terminal_outcome"])
    op.create_table(
        "ops_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("safe_summary", sa.String(length=500), nullable=False),
        sa.Column("safe_context", sa.Text(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ops_incidents_incident_key", "ops_incidents", ["incident_key"])
    op.create_index("ix_ops_incidents_status", "ops_incidents", ["status"])
    op.create_table(
        "review_status_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_owner", sa.String(length=255), nullable=False),
        sa.Column("repository_name", sa.String(length=255), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("notice_code", sa.String(length=64), nullable=False),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "repository_owner", "repository_name", "pull_request_number", "head_sha", "notice_code",
            name="uq_review_status_notice",
        ),
    )


def downgrade() -> None:
    op.drop_table("review_status_notices")
    op.drop_index("ix_ops_incidents_status", table_name="ops_incidents")
    op.drop_index("ix_ops_incidents_incident_key", table_name="ops_incidents")
    op.drop_table("ops_incidents")
    op.drop_index("ix_review_jobs_terminal_outcome", table_name="review_jobs")
    for name in (
        "reaction_cleanup_status",
        "terminal_outcome",
        "executor_runtime_version",
        "codex_cli_version",
        "schema_hash",
        "model_catalog_version",
        "reasoning_source",
        "model_source",
    ):
        op.drop_column("review_jobs", name)
