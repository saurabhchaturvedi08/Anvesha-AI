"""Project chat: conversations with the Research Assistant about a project's evidence."""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey

CHAT_ROLES = ("user", "assistant")
# pending: the assistant is still writing this reply. failed: it couldn't (see error_*).
CHAT_MESSAGE_STATUSES = ("pending", "complete", "failed")


class ChatThread(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "chat_threads"
    __table_args__ = (Index("ix_chat_threads_project_id_updated_at", "project_id", "updated_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200))


class ChatMessage(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(f"role IN {CHAT_ROLES!r}", name="role"),
        CheckConstraint(f"status IN {CHAT_MESSAGE_STATUSES!r}", name="status"),
        Index("ix_chat_messages_thread_id_created_at", "thread_id", "created_at"),
        # One reply at a time per conversation: answers build on the previous ones.
        Index(
            "uq_chat_messages_thread_id_pending",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="complete")
    content: Mapped[str] = mapped_column(Text, default="")
    # [{"number", "source_id", "title", "url", "quotes": [{"quote", "page"}]}]
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Progress while pending ({"activity": [...]}), then usage and the tools used.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
