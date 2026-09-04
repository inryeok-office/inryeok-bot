"""add review domain settings and job diagnostics

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "global_review_settings",
        sa.Column("review_domain_mode", sa.String(16), nullable=False, server_default="AUTO"),
    )
    op.add_column(
        "global_review_settings",
        sa.Column("manual_review_domains", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("repository_settings", sa.Column("override_review_domain_mode", sa.String(16)))
    op.add_column("repository_settings", sa.Column("override_manual_review_domains", sa.Text()))
    for name, type_ in (
        ("detected_review_domains", sa.Text()),
        ("effective_review_domains", sa.Text()),
        ("detection_reasons", sa.Text()),
        ("prompt_version", sa.String(32)),
    ):
        op.add_column("review_jobs", sa.Column(name, type_))


def downgrade() -> None:
    for name in (
        "prompt_version",
        "detection_reasons",
        "effective_review_domains",
        "detected_review_domains",
    ):
        op.drop_column("review_jobs", name)
    op.drop_column("repository_settings", "override_manual_review_domains")
    op.drop_column("repository_settings", "override_review_domain_mode")
    op.drop_column("global_review_settings", "manual_review_domains")
    op.drop_column("global_review_settings", "review_domain_mode")
