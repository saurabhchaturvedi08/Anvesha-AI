import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey

WORKSPACE_ROLES = ("owner", "admin", "member")


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    # Stored lowercased; see anvesha.tenancy.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    # The identity provider's id for this user (the JWT "sub" from the web app's sign-in).
    # None for the built-in user of single-user mode.
    auth_subject: Mapped[str | None] = mapped_column(String(255), unique=True)


class Workspace(UUIDPrimaryKey, Timestamps, Base):
    """The tenant boundary. Every research record belongs to exactly one workspace."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200))
    # True for the workspace created automatically for each user.
    personal: Mapped[bool] = mapped_column(default=False, server_default="false")


class WorkspaceMember(Timestamps, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (CheckConstraint(f"role IN {WORKSPACE_ROLES!r}", name="role"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="owner")


class WorkspaceInvitation(UUIDPrimaryKey, Base):
    """A link that lets whoever opens it (signed in) join a workspace.

    Only the token's hash is stored. The link is shown once, to the admin who created
    it, to pass on (Anvesha doesn't send email yet).
    """

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role"),
        Index("ix_workspace_invitations_workspace_id_created_at", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    # Who it's meant for, shown to admins and on the invitation page.
    email: Mapped[str | None] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20), default="member")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateLimitCounter(Base):
    """Requests counted per key (e.g. "runs:<workspace id>") in fixed time windows.

    Kept in Postgres so every API replica shares the same counts.
    """

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
