"""OAuth sign-in for MCP clients: registered clients, authorization codes, refresh tokens,
and OAuth-issued access tokens.

Revision ID: 8c3f1a6e4b27
Revises: 5b1e7c9d2a40
Create Date: 2026-11-26 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8c3f1a6e4b27"
down_revision: str | None = "5b1e7c9d2a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=True),
        sa.Column("client_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_oauth_clients")),
    )

    op.add_column("access_tokens", sa.Column("client_id", sa.String(length=64), nullable=True))
    op.add_column(
        "access_tokens",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column("access_tokens", sa.Column("resource", sa.Text(), nullable=True))
    op.create_index(
        op.f("ix_access_tokens_client_id"), "access_tokens", ["client_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_access_tokens_client_id_oauth_clients"),
        "access_tokens",
        "oauth_clients",
        ["client_id"],
        ["client_id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            name=op.f("fk_oauth_authorization_codes_client_id_oauth_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_oauth_authorization_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_oauth_authorization_codes_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("code_hash", name=op.f("pk_oauth_authorization_codes")),
    )
    op.create_index(
        op.f("ix_oauth_authorization_codes_client_id"),
        "oauth_authorization_codes",
        ["client_id"],
        unique=False,
    )

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("access_token_id", sa.UUID(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["access_token_id"],
            ["access_tokens.id"],
            name=op.f("fk_oauth_refresh_tokens_access_token_id_access_tokens"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            name=op.f("fk_oauth_refresh_tokens_client_id_oauth_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_oauth_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_oauth_refresh_tokens_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_oauth_refresh_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_oauth_refresh_tokens_client_id"),
        "oauth_refresh_tokens",
        ["client_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_refresh_tokens_client_id"), table_name="oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")
    op.drop_index(
        op.f("ix_oauth_authorization_codes_client_id"), table_name="oauth_authorization_codes"
    )
    op.drop_table("oauth_authorization_codes")
    # OAuth-issued tokens can't outlive their clients.
    op.execute("DELETE FROM access_tokens WHERE client_id IS NOT NULL")
    op.drop_constraint(
        op.f("fk_access_tokens_client_id_oauth_clients"), "access_tokens", type_="foreignkey"
    )
    op.drop_index(op.f("ix_access_tokens_client_id"), table_name="access_tokens")
    op.drop_column("access_tokens", "resource")
    op.drop_column("access_tokens", "scopes")
    op.drop_column("access_tokens", "client_id")
    op.drop_table("oauth_clients")
