import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)


class ChatThreadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)


class ChatThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatQuote(BaseModel):
    quote: str
    statement: str | None = None
    page: int | None = None
    # The run finding this quote comes from; None for quotes the assistant checked itself.
    finding_id: uuid.UUID | None = None


class ChatCitation(BaseModel):
    number: int
    source_id: uuid.UUID
    title: str | None
    url: str | None
    quotes: list[ChatQuote]


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    role: Literal["user", "assistant"]
    status: Literal["pending", "complete", "failed"]
    content: str
    citations: list[ChatCitation]
    details: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime


class ChatThreadDetail(ChatThreadRead):
    messages: list[ChatMessageRead]


class ChatMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=4000)


class ChatExchange(BaseModel):
    """Your message, and the assistant's reply (pending until it is written)."""

    user_message: ChatMessageRead
    assistant_message: ChatMessageRead
