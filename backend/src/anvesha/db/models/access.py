"""Credentials that let outside clients (MCP clients such as Claude Code, Cursor, claude.ai
or ChatGPT) act for a user in a workspace.

Two ways in: a personal access token the user creates and pastes into the client, or
OAuth sign-in, where the client registers itself and the user approves it once.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey


class OAuthClient(Timestamps, Base):
    """An application registered through OAuth dynamic client registration (RFC 7591)."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_name: Mapped[str | None] = mapped_column(String(200))
    # The registered metadata (redirect URIs, grant types...), without the secret.
    client_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Encrypted with the deployment's master key: the token endpoint compares it as-is.
    secret_encrypted: Mapped[str | None] = mapped_column(Text)


class AccessToken(UUIDPrimaryKey, Timestamps, Base):
    """A bearer token. Only its SHA-256 hash is stored; the token is shown once, at creation.

    Personal tokens have no client. Tokens issued through OAuth belong to the client that
    was approved, and expire within an hour (the client refreshes them).
    """

    __tablename__ = "access_tokens"
    __table_args__ = (
        Index("ix_access_tokens_workspace_id_created_at", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), index=True
    )
    # A label so the user can tell their tokens apart, e.g. "Claude Code on my laptop".
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The token's first characters, safe to display ("anv_k3Xq…").
    prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # The resource (RFC 8707) an OAuth token was issued for: this server's /mcp URL.
    resource: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Refreshed at most once a minute, so busy clients don't write on every request.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthAuthorizationCode(Base):
    """A one-time code from an approved sign-in, exchanged for tokens within minutes."""

    __tablename__ = "oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(Text)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(Boolean)
    resource: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthRefreshToken(UUIDPrimaryKey, Timestamps, Base):
    """Lets an approved client get new access tokens. Replaced on every use (rotation)."""

    __tablename__ = "oauth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The access token issued with it; revoking either revokes both.
    access_token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("access_tokens.id", ondelete="SET NULL")
    )
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    resource: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
