"""store bounded redacted Codex failure metadata"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("error_stage", sa.String(length=32), nullable=True))
    op.add_column("review_jobs", sa.Column("error_signature", sa.String(length=64), nullable=True))
    op.add_column("review_jobs", sa.Column("correlation_id", sa.String(length=64), nullable=True))
    op.add_column("review_jobs", sa.Column("stderr_byte_length", sa.Integer(), nullable=True))
    op.add_column("review_jobs", sa.Column("redacted_diagnostic", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_jobs", "redacted_diagnostic")
    op.drop_column("review_jobs", "stderr_byte_length")
    op.drop_column("review_jobs", "correlation_id")
    op.drop_column("review_jobs", "error_signature")
    op.drop_column("review_jobs", "error_stage")
