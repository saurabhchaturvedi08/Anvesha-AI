import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceAddFromURL(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_type: Literal["html", "pdf", "text"]
    origin: Literal["web", "upload"]
    added_by: Literal["user", "agent"]
    url: str | None
    title: str | None
    authors: list[str]
    published: str | None
    site_name: str | None
    doi: str | None
    content_type: str
    filename: str | None
    byte_size: int
    char_count: int
    details: dict[str, Any]
    embedding_status: Literal["pending", "ready", "skipped", "failed"]
    embedding_model: str | None
    embedding_error: str | None
    fetched_at: datetime | None
    created_at: datetime


class SourceCreated(SourceRead):
    # False when the URL or identical text was already saved; the existing source is returned.
    created: bool


class SourceText(BaseModel):
    source_id: uuid.UUID
    offset: int
    next_offset: int | None
    total_chars: int
    text: str


class SourceSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=50)
    source_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class ChunkHitRead(BaseModel):
    chunk_id: int
    source_id: uuid.UUID
    source_title: str | None
    source_url: str | None
    char_start: int
    char_end: int
    text: str
    score: float
    matched_by: list[Literal["keyword", "semantic"]]


class SourceSearchResponse(BaseModel):
    mode: Literal["hybrid", "keyword"]
    semantic_note: str | None
    hits: list[ChunkHitRead]


class QuoteVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    quote: str = Field(min_length=1, max_length=5000)


class QuoteSpan(BaseModel):
    start: int
    end: int
    page: int | None
    text: str


class QuoteVerifyResponse(BaseModel):
    source_id: uuid.UUID
    status: Literal["exact", "normalized", "fragments", "not_found", "too_short"]
    supported: bool
    spans: list[QuoteSpan]
    context: str | None


class EmbedResponse(BaseModel):
    source_id: uuid.UUID
    embedding_status: Literal["pending", "ready", "skipped", "failed"]
    embedding_model: str | None
    embedding_error: str | None
