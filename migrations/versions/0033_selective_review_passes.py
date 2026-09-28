"""Durable process reservations and nullable pass diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_runs", sa.Column("partial_review", sa.Boolean(), nullable=True))
    for name, kind in (
        ("pass_type", sa.String(32)),
        ("dedup_fingerprint", sa.String(64)),
        ("path_present", sa.Boolean()),
        ("line_present", sa.Boolean()),
    ):
        op.add_column("review_finding_diagnostics", sa.Column(name, kind, nullable=True))
    op.create_table(
        "review_passes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("review_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pass_type", sa.String(32), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("effort", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("raw_count", sa.Integer(), nullable=True),
        sa.Column("accepted_count", sa.Integer(), nullable=True),
        sa.Column("rejected_count", sa.Integer(), nullable=True),
        sa.Column("contribution_count", sa.Integer(), nullable=True),
        sa.Column("process_count", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.UniqueConstraint("job_id", "pass_type", name="uq_review_pass_job_type"),
        sa.CheckConstraint(
            "state IN ('RESERVED','SUCCEEDED','FAILED','UNKNOWN','SKIPPED')",
            name="ck_review_pass_state",
        ),
    )
    op.create_index("ix_review_passes_job_id", "review_passes", ["job_id"])
    op.create_index("ix_review_passes_started_at", "review_passes", ["started_at"])


def downgrade() -> None:
    op.drop_table("review_passes")
    for name in ("line_present", "path_present", "dedup_fingerprint", "pass_type"):
        op.drop_column("review_finding_diagnostics", name)
    op.drop_column("review_runs", "partial_review")
