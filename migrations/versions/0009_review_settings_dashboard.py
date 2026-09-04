"""add global settings, repository overrides, and admin audit logs

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "global_review_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_review_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("command_review_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("language", sa.String(8), nullable=False, server_default="ko"),
        sa.Column("review_profile", sa.String(32), nullable=False, server_default="BALANCED"),
        sa.Column("model", sa.String(128)),
        sa.Column("max_findings", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("minimum_confidence", sa.Float(), nullable=False, server_default="0.9"),
        sa.Column("include_low_severity", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("minimum_severity", sa.String(16), nullable=False, server_default="MEDIUM"),
        sa.Column("enabled_categories", sa.Text(), nullable=False, server_default=""),
        sa.Column("ignored_paths", sa.Text(), nullable=False, server_default=""),
        sa.Column("review_on_opened", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_on_reopened", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "review_on_ready_for_review", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("review_on_synchronize", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("codex_timeout_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("INSERT INTO global_review_settings (id) VALUES (1)")
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_login", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for name, type_ in (
        ("override_enabled", sa.Boolean()),
        ("override_auto_review_enabled", sa.Boolean()),
        ("override_command_review_enabled", sa.Boolean()),
        ("override_language", sa.String(8)),
        ("override_review_profile", sa.String(32)),
        ("override_model", sa.String(128)),
        ("override_max_findings", sa.Integer()),
        ("override_minimum_confidence", sa.Float()),
        ("override_include_low_severity", sa.Boolean()),
        ("override_ignored_paths", sa.Text()),
        ("override_timeout_seconds", sa.Integer()),
        ("override_minimum_severity", sa.String(16)),
        ("override_enabled_categories", sa.Text()),
        ("override_review_on_opened", sa.Boolean()),
        ("override_review_on_reopened", sa.Boolean()),
        ("override_review_on_ready_for_review", sa.Boolean()),
        ("override_review_on_synchronize", sa.Boolean()),
    ):
        op.add_column("repository_settings", sa.Column(name, type_))
    op.add_column("review_jobs", sa.Column("retry_of_job_id", sa.Integer()))
    op.add_column("review_jobs", sa.Column("superseded_by_head_sha", sa.String(64)))


def downgrade() -> None:
    for name in (
        "override_timeout_seconds",
        "override_ignored_paths",
        "override_include_low_severity",
        "override_minimum_confidence",
        "override_max_findings",
        "override_model",
        "override_review_profile",
        "override_language",
        "override_command_review_enabled",
        "override_auto_review_enabled",
        "override_enabled",
        "override_review_on_synchronize",
        "override_review_on_ready_for_review",
        "override_review_on_reopened",
        "override_review_on_opened",
        "override_enabled_categories",
        "override_minimum_severity",
    ):
        op.drop_column("repository_settings", name)
    op.drop_column("review_jobs", "superseded_by_head_sha")
    op.drop_column("review_jobs", "retry_of_job_id")
    op.drop_table("admin_audit_logs")
    op.drop_table("global_review_settings")
