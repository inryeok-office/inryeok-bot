"""Add additive collaboration-review policy and safe observation metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, columns in (
        (
            "global_review_settings",
            (
                ("minimum_review_type", sa.String(16)), ("max_inline_comments", sa.Integer()),
                ("allow_suggestions", sa.Boolean()), ("allow_questions", sa.Boolean()),
                ("allow_positive_fallback", sa.Boolean()), ("allow_review_summary", sa.Boolean()),
                ("allow_repository_config", sa.Boolean()), ("allow_suggested_changes", sa.Boolean()),
            ),
        ),
        (
            "repository_settings",
            (
                ("override_minimum_review_type", sa.String(16)), ("override_max_inline_comments", sa.Integer()),
                ("override_allow_suggestions", sa.Boolean()), ("override_allow_questions", sa.Boolean()),
                ("override_allow_positive_fallback", sa.Boolean()), ("override_allow_suggested_changes", sa.Boolean()),
            ),
        ),
        (
            "review_runs",
            (
                ("review_type_counts", sa.JSON()), ("severity_counts", sa.JSON()),
                ("no_reviewable_reason", sa.String(64)), ("comment_budget_accepted_count", sa.Integer()),
                ("suggested_patch_fallback", sa.Boolean()),
            ),
        ),
        (
            "finding_records",
            (("review_type", sa.String(16)), ("blocking", sa.Boolean()), ("suggested_patch", sa.Text()), ("style_guide_reference", sa.String(500))),
        ),
    ):
        for name, kind in columns:
            op.add_column(table, sa.Column(name, kind, nullable=True))
    op.add_column("review_finding_diagnostics", sa.Column("review_type", sa.String(16), nullable=True))
    op.create_table(
        "review_feedback_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.String(100), nullable=True),
        sa.Column("repository_owner", sa.String(255), nullable=False),
        sa.Column("repository_name", sa.String(255), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=True),
        sa.Column("finding_fingerprint", sa.String(64), nullable=True),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("review_type", sa.String(16), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_login", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("delivery_id", name="uq_review_feedback_delivery"),
    )


def downgrade() -> None:
    op.drop_table("review_feedback_events")
    op.drop_column("review_finding_diagnostics", "review_type")
    for table, names in (
        ("finding_records", ("style_guide_reference", "suggested_patch", "blocking", "review_type")),
        ("review_runs", ("suggested_patch_fallback", "comment_budget_accepted_count", "no_reviewable_reason", "severity_counts", "review_type_counts")),
        ("repository_settings", ("override_allow_suggested_changes", "override_allow_positive_fallback", "override_allow_questions", "override_allow_suggestions", "override_max_inline_comments", "override_minimum_review_type")),
        ("global_review_settings", ("allow_suggested_changes", "allow_repository_config", "allow_review_summary", "allow_positive_fallback", "allow_questions", "allow_suggestions", "max_inline_comments", "minimum_review_type")),
    ):
        for name in names:
            op.drop_column(table, name)
