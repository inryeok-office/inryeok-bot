"""Store safe per-finding rejection metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_finding_diagnostics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("review_run_id", sa.Integer(), nullable=False),
        sa.Column("finding_index", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("relation_to_change", sa.String(length=32), nullable=True),
        sa.Column("introduced_by_pr", sa.Boolean(), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rejection_stage", sa.String(length=32), nullable=False),
        sa.Column("rejection_reason", sa.String(length=64), nullable=False),
        sa.Column("path_is_changed", sa.Boolean(), nullable=False),
        sa.Column("changed_symbol_present", sa.Boolean(), nullable=False),
        sa.Column("causal_evidence_present", sa.Boolean(), nullable=False),
        sa.Column("expected_anchor_kind", sa.String(length=32), nullable=True),
        sa.Column("anchor_matches", sa.Boolean(), nullable=True),
        sa.Column("diagnostic_schema_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["job_id"], ["review_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "review_run_id", "finding_index", name="uq_review_finding_diagnostic_index"
        ),
    )
    op.create_index(
        "ix_review_finding_diagnostics_run",
        "review_finding_diagnostics",
        ["review_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_finding_diagnostics_run", table_name="review_finding_diagnostics")
    op.drop_table("review_finding_diagnostics")
