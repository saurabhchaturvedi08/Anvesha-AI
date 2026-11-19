import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    # Stored lowercased; see anvesha.tenancy.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))


class Workspace(UUIDPrimaryKey, Timestamps, Base):
    """The tenant boundary. Every research record belongs to exactly one workspace."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200))


class WorkspaceMember(Timestamps, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (CheckConstraint("role IN ('owner', 'admin', 'member')", name="role"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="owner")
