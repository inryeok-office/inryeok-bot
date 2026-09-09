"""Persist bounded structured review failure diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("error_category", sa.String(length=32), nullable=True))
    op.add_column("review_jobs", sa.Column("retry_policy", sa.String(length=32), nullable=True))
    op.add_column("review_jobs", sa.Column("user_action_required", sa.Boolean(), nullable=True))
    op.add_column("review_jobs", sa.Column("user_error_message", sa.Text(), nullable=True))
    op.add_column("review_jobs", sa.Column("operator_error_message", sa.Text(), nullable=True))
    op.add_column("review_jobs", sa.Column("output_field", sa.String(length=128), nullable=True))
    op.add_column("review_jobs", sa.Column("validation_type", sa.String(length=64), nullable=True))
    op.add_column("review_jobs", sa.Column("http_status", sa.Integer(), nullable=True))
    op.create_index("ix_review_jobs_error_category", "review_jobs", ["error_category"])


def downgrade() -> None:
    op.drop_index("ix_review_jobs_error_category", table_name="review_jobs")
    for name in (
        "http_status",
        "validation_type",
        "output_field",
        "operator_error_message",
        "user_error_message",
        "user_action_required",
        "retry_policy",
        "error_category",
    ):
        op.drop_column("review_jobs", name)
