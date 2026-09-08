"""Add durable GitHub installation and repository numeric identity metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger()),
        sa.Column("account_login", sa.String(255)),
        sa.Column("account_type", sa.String(32)),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("repository_selection", sa.String(32)),
        sa.Column("installed_at", sa.DateTime(timezone=True)),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("uninstalled_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("github_installation_id", name="uq_github_installations_external_id"),
    )
    op.create_index(
        "ix_github_installations_github_installation_id",
        "github_installations",
        ["github_installation_id"],
    )
    op.create_index("ix_github_installations_status", "github_installations", ["status"])
    op.add_column(
        "repository_settings",
        sa.Column("installation_fk_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "repository_settings",
        sa.Column("github_repository_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "repository_settings", sa.Column("version", sa.Integer(), nullable=True, server_default="1")
    )
    op.add_column(
        "global_review_settings",
        sa.Column("version", sa.Integer(), nullable=True, server_default="1"),
    )
    op.execute(sa.text("UPDATE repository_settings SET version = 1 WHERE version IS NULL"))
    op.execute(sa.text("UPDATE global_review_settings SET version = 1 WHERE version IS NULL"))
    op.alter_column("repository_settings", "version", nullable=False, server_default=None)
    op.alter_column("global_review_settings", "version", nullable=False, server_default=None)
    op.create_foreign_key(
        "fk_repository_settings_installation_fk_id",
        "repository_settings",
        "github_installations",
        ["installation_fk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_repository_settings_installation_fk_id",
        "repository_settings",
        ["installation_fk_id"],
    )
    op.create_index(
        "ix_repository_settings_github_repository_id",
        "repository_settings",
        ["github_repository_id"],
    )
    op.create_unique_constraint(
        "uq_repository_settings_installation_github_id",
        "repository_settings",
        ["installation_fk_id", "github_repository_id"],
    )

    # Preserve every existing installation identity.  Historical rows do not
    # contain account metadata or repository numeric IDs; those remain NULL
    # until the next signed installation/repository synchronization supplies it.
    op.execute(
        sa.text(
            "INSERT INTO github_installations "
            "(github_installation_id, status, version) "
            "SELECT DISTINCT rs.installation_id, 'ACTIVE', 1 "
            "FROM repository_settings rs "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM github_installations gi "
            "WHERE gi.github_installation_id = rs.installation_id)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE repository_settings SET installation_fk_id = ("
            "SELECT gi.id FROM github_installations gi "
            "WHERE gi.github_installation_id = repository_settings.installation_id) "
            "WHERE installation_fk_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_repository_settings_installation_github_id",
        "repository_settings",
        type_="unique",
    )
    op.drop_column("global_review_settings", "version")
    op.drop_column("repository_settings", "version")
    op.drop_index("ix_repository_settings_github_repository_id", table_name="repository_settings")
    op.drop_index("ix_repository_settings_installation_fk_id", table_name="repository_settings")
    op.drop_constraint(
        "fk_repository_settings_installation_fk_id", "repository_settings", type_="foreignkey"
    )
    op.drop_column("repository_settings", "github_repository_id")
    op.drop_column("repository_settings", "installation_fk_id")
    op.drop_index("ix_github_installations_status", table_name="github_installations")
    op.drop_index(
        "ix_github_installations_github_installation_id", table_name="github_installations"
    )
    op.drop_table("github_installations")
