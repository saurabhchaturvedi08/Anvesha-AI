"""Credentials that let outside clients (MCP clients such as Claude Code or Cursor) act
for a user in a workspace."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey


class AccessToken(UUIDPrimaryKey, Timestamps, Base):
    """A bearer token. Only its SHA-256 hash is stored; the token is shown once, at creation."""

    __tablename__ = "access_tokens"
    __table_args__ = (
        Index("ix_access_tokens_workspace_id_created_at", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # A label so the user can tell their tokens apart, e.g. "Claude Code on my laptop".
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The token's first characters, safe to display ("anv_k3Xq…").
    prefix: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Refreshed at most once a minute, so busy clients don't write on every request.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
