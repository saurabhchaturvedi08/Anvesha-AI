"""Multi-user sign-in: identity-provider subjects, personal workspaces, invitations, rate
limit counters, and a schema for the web app's auth tables.

Revision ID: 4e2a9d71c3b5
Revises: 8c3f1a6e4b27
Create Date: 2026-11-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e2a9d71c3b5"
down_revision: str | None = "8c3f1a6e4b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Better Auth (in the web app) keeps its users, sessions and signing keys here, apart
    # from Anvesha's own tables.
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")

    op.add_column("users", sa.Column("auth_subject", sa.String(length=255), nullable=True))
    op.create_unique_constraint(op.f("uq_users_auth_subject"), "users", ["auth_subject"])

    op.add_column(
        "workspaces",
        sa.Column("personal", sa.Boolean(), server_default="false", nullable=False),
    )
    # Until now every workspace was a user's automatic one.
    op.execute("UPDATE workspaces SET personal = true")

    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'member')", name=op.f("ck_workspace_invitations_role")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_invitations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["users.id"],
            name=op.f("fk_workspace_invitations_invited_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_id"],
            ["users.id"],
            name=op.f("fk_workspace_invitations_accepted_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_workspace_invitations_token_hash")),
    )
    op.create_index(
        "ix_workspace_invitations_workspace_id_created_at",
        "workspace_invitations",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "rate_limit_counters",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window_start", name=op.f("pk_rate_limit_counters")),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_counters")
    op.drop_index(
        "ix_workspace_invitations_workspace_id_created_at", table_name="workspace_invitations"
    )
    op.drop_table("workspace_invitations")
    op.drop_column("workspaces", "personal")
    op.drop_constraint(op.f("uq_users_auth_subject"), "users", type_="unique")
    op.drop_column("users", "auth_subject")
    # The auth schema may hold the web app's users; it is left in place on purpose.
