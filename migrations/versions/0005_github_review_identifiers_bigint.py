"""store GitHub review identifiers as bigint

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "review_runs",
        "github_review_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="github_review_id::bigint",
    )
    op.alter_column(
        "finding_records",
        "github_comment_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="github_comment_id::bigint",
    )


def downgrade() -> None:
    op.alter_column(
        "finding_records",
        "github_comment_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="github_comment_id::integer",
    )
    op.alter_column(
        "review_runs",
        "github_review_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="github_review_id::integer",
    )
