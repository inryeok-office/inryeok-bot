"""initial tables"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum(
        "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", name="jobstatus", native_enum=False
    )
    trigger = sa.Enum("AUTO", "COMMAND", "RETRY", name="triggertype", native_enum=False)
    op.create_table(
        "repository_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("repository_owner", sa.String(255), nullable=False),
        sa.Column("repository_name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_review", sa.Boolean(), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=False),
        sa.Column("max_findings", sa.Integer(), nullable=False),
        sa.Column("include_low_severity", sa.Boolean(), nullable=False),
        sa.Column("ignore_patterns", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("min_confidence >= 0 AND min_confidence <= 1", name="ck_min_confidence"),
        sa.CheckConstraint("max_findings >= 1 AND max_findings <= 50", name="ck_max_findings"),
        sa.UniqueConstraint(
            "installation_id", "repository_owner", "repository_name", name="uq_repository_settings"
        ),
    )
    op.create_table(
        "review_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.String(100), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("repository_owner", sa.String(255), nullable=False),
        sa.Column("repository_name", sa.String(255), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.String(64), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("trigger_type", trigger, nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("delivery_id"),
        sa.UniqueConstraint(
            "repository_owner",
            "repository_name",
            "pull_request_number",
            "head_sha",
            "trigger_type",
            name="uq_review_policy",
        ),
    )
    op.create_index("ix_review_jobs_status", "review_jobs", ["status"])
    op.create_table(
        "review_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("review_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_sha", sa.String(64), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("github_review_id", sa.Integer()),
        sa.Column("reviewed_file_count", sa.Integer(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "finding_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_run_id",
            sa.Integer(),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("github_comment_id", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("finding_records")
    op.drop_table("review_runs")
    op.drop_index("ix_review_jobs_status", table_name="review_jobs")
    op.drop_table("review_jobs")
    op.drop_table("repository_settings")
