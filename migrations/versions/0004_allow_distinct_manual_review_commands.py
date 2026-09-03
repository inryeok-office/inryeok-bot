"""allow distinct manual review commands for the same head

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_review_policy", "review_jobs", type_="unique")
    op.create_index(
        "uq_review_policy_non_command",
        "review_jobs",
        [
            "repository_owner",
            "repository_name",
            "pull_request_number",
            "head_sha",
            "trigger_type",
        ],
        unique=True,
        postgresql_where=sa.text("trigger_type <> 'COMMAND'"),
    )


def downgrade() -> None:
    op.drop_index("uq_review_policy_non_command", table_name="review_jobs")
    op.create_unique_constraint(
        "uq_review_policy",
        "review_jobs",
        [
            "repository_owner",
            "repository_name",
            "pull_request_number",
            "head_sha",
            "trigger_type",
        ],
    )
