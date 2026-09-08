"""Record whether global quality thresholds inherit the selected profile."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "global_review_settings",
        sa.Column("profile_defaults_inherited", sa.Boolean(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE global_review_settings SET profile_defaults_inherited = "
            "CASE WHEN review_profile = 'THOROUGH' AND minimum_confidence = 0.9 "
            "AND minimum_severity = 'LOW' AND max_findings = 30 "
            "AND include_low_severity = false THEN true ELSE false END"
        )
    )
    op.alter_column(
        "global_review_settings",
        "profile_defaults_inherited",
        nullable=False,
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.drop_column("global_review_settings", "profile_defaults_inherited")
