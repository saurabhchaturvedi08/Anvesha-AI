import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey

DEPTHS = ("quick", "standard", "deep")


class Project(UUIDPrimaryKey, Timestamps, Base):
    """A research question and everything gathered for it (runs, sources, findings, reports)."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(f"depth IN {DEPTHS!r}", name="depth"),
        Index("ix_projects_workspace_id_created_at", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(255))
    objective: Mapped[str] = mapped_column(Text)
    depth: Mapped[str] = mapped_column(String(16), default="standard")
