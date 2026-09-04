"""add review run diagnostics

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("changed_files_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_lines_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("codex_exit_code", sa.Integer(), nullable=True),
        sa.Column("codex_output_present", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_valid_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_file_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_line_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("severity_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deduplicated_findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_findings_count", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in columns:
        op.add_column("review_runs", column)


def downgrade() -> None:
    for name in (
        "published_findings_count",
        "deduplicated_findings_count",
        "evidence_findings_count",
        "severity_findings_count",
        "confidence_findings_count",
        "changed_line_findings_count",
        "changed_file_findings_count",
        "schema_valid_findings_count",
        "raw_findings_count",
        "codex_output_present",
        "codex_exit_code",
        "changed_lines_count",
        "changed_files_count",
    ):
        op.drop_column("review_runs", name)
