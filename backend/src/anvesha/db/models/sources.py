import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey

SOURCE_TYPES = ("html", "pdf", "text")
SOURCE_ORIGINS = ("web", "upload")
EMBEDDING_STATUSES = ("pending", "ready", "skipped", "failed")
# Full-text search configuration. English stemming helps recall for English sources;
# other languages still match on exact words.
FTS_CONFIG = "english"


class Source(UUIDPrimaryKey, Timestamps, Base):
    """A document gathered for a project: a fetched web page or PDF, or an upload.

    ``text`` is the exact extracted text. Chunk offsets and quote verification refer to it,
    so it is never rewritten after insert.
    """

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(f"source_type IN {SOURCE_TYPES!r}", name="source_type"),
        CheckConstraint(f"origin IN {SOURCE_ORIGINS!r}", name="origin"),
        CheckConstraint(f"embedding_status IN {EMBEDDING_STATUSES!r}", name="embedding_status"),
        CheckConstraint("added_by IN ('user', 'agent')", name="added_by"),
        UniqueConstraint("project_id", "text_hash", name="uq_sources_project_id_text_hash"),
        Index(
            "uq_sources_project_id_url",
            "project_id",
            "url",
            unique=True,
            postgresql_where=text("url IS NOT NULL"),
        ),
        Index("ix_sources_project_id_created_at", "project_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(16))
    added_by: Mapped[str] = mapped_column(String(16), default="user")
    # Canonical URL after redirects (web sources only).
    url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(1000))
    authors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # As found in the source; may be partial ("2021", "2021-03").
    published: Mapped[str | None] = mapped_column(String(32))
    site_name: Mapped[str | None] = mapped_column(String(255))
    doi: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str | None] = mapped_column(String(255))
    # Key of the original file in storage (PDFs and uploads); NULL for web pages.
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    byte_size: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    char_count: Mapped[int] = mapped_column(Integer)
    # Extra facts: PDF page offsets, scholarly metadata, truncation notes.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    embedding_status: Mapped[str] = mapped_column(String(16), default="pending")
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    embedding_error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceChunk(Base):
    """A passage of a source, indexed for keyword (tsvector) and semantic (vector) search."""

    __tablename__ = "source_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_source_chunks_source_id_ordinal"),
        Index("ix_source_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    # Offsets into Source.text: text == source.text[char_start:char_end].
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed(f"to_tsvector('{FTS_CONFIG}', text)", persisted=True)
    )
    # No fixed dimension: each workspace picks its own embedding model. Searches only
    # compare vectors from the same model (embedding_model), so sizes always match.
    # Per-project scans are small enough that exact search needs no ANN index.
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(255))
