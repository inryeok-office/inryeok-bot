"""align review settings constraints with ORM metadata

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("global_review_settings", "updated_at", nullable=False)
    op.alter_column("admin_audit_logs", "created_at", nullable=False)
    op.create_foreign_key(
        "fk_review_jobs_retry_of_job_id",
        "review_jobs",
        "review_jobs",
        ["retry_of_job_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_review_jobs_retry_of_job_id", "review_jobs", type_="foreignkey")
    op.alter_column("admin_audit_logs", "created_at", nullable=True)
    op.alter_column("global_review_settings", "updated_at", nullable=True)
