"""Add PostgreSQL model catalog and verification evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "codex_model_catalog",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("purpose_ko", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("description_ko", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "availability_status", sa.String(length=16), nullable=False, server_default="CANDIDATE"
        ),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="OPERATOR"),
        sa.Column("supported_efforts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("default_effort", sa.String(length=16), nullable=True),
        sa.Column("verified_cli_version", sa.String(length=64), nullable=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=300), nullable=True),
        sa.Column("verification_id", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_id", name="uq_codex_model_catalog_model_id"),
    )
    op.create_index(
        "ix_codex_model_catalog_model_id", "codex_model_catalog", ["model_id"], unique=False
    )
    op.create_index(
        "ix_codex_model_catalog_availability_status",
        "codex_model_catalog",
        ["availability_status"],
        unique=False,
    )
    op.create_table(
        "codex_model_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("verification_id", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("cli_version", sa.String(length=64), nullable=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=True),
        sa.Column("execution_id", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("process_exit_code", sa.Integer(), nullable=True),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("safe_error_category", sa.String(length=64), nullable=True),
        sa.Column(
            "diagnostic_extraction_failed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("actor_login", sa.String(length=255), nullable=True),
        sa.UniqueConstraint("verification_id", name="uq_codex_model_verification_id"),
    )
    op.create_index(
        "ix_codex_model_verifications_model_id", "codex_model_verifications", ["model_id"]
    )
    op.create_index(
        "ix_codex_model_verifications_model_effort",
        "codex_model_verifications",
        ["model_id", "reasoning_effort"],
    )
    op.create_index("ix_codex_model_verifications_status", "codex_model_verifications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_codex_model_verifications_status", table_name="codex_model_verifications")
    op.drop_index(
        "ix_codex_model_verifications_model_effort", table_name="codex_model_verifications"
    )
    op.drop_index("ix_codex_model_verifications_model_id", table_name="codex_model_verifications")
    op.drop_table("codex_model_verifications")
    op.drop_index("ix_codex_model_catalog_availability_status", table_name="codex_model_catalog")
    op.drop_index("ix_codex_model_catalog_model_id", table_name="codex_model_catalog")
    op.drop_table("codex_model_catalog")
