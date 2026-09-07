"""retain safe Codex exit code on failed jobs"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_jobs", sa.Column("codex_exit_code", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_jobs", "codex_exit_code")
