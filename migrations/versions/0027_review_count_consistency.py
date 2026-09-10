"""Persist canonical publish and rerun comparison counts."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("scope_valid_findings_count", sa.Integer()),
        ("rejected_findings_count", sa.Integer()),
        ("comparison_new_count", sa.Integer()),
        ("comparison_still_count", sa.Integer()),
        ("comparison_not_detected_count", sa.Integer()),
    ):
        op.add_column("review_runs", sa.Column(name, column, nullable=False, server_default="0"))
    op.add_column(
        "review_runs",
        sa.Column("published_finding_fingerprints", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_runs", "published_finding_fingerprints")
    op.drop_column("review_runs", "comparison_not_detected_count")
    op.drop_column("review_runs", "comparison_still_count")
    op.drop_column("review_runs", "comparison_new_count")
    op.drop_column("review_runs", "rejected_findings_count")
    op.drop_column("review_runs", "scope_valid_findings_count")
