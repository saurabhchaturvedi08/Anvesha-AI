from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class SearchProviderRead(BaseModel):
    id: str
    name: str
    requires_api_key: bool
    workspace_configurable: bool
    free: bool
    docs_url: str
    configured: bool
    source: Literal["workspace", "deployment"] | None
    status: Literal["valid", "invalid", "unverified"] | None
    key_hint: str | None
    last_checked_at: datetime | None
    last_error: str | None
    is_default: bool


class SearchCredentialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(max_length=4096)

    @model_validator(mode="after")
    def _not_blank(self) -> "SearchCredentialWrite":
        if not self.api_key.get_secret_value().strip():
            raise ValueError("api_key must not be empty")
        return self


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)
    provider: str | None = Field(default=None, max_length=32)


class WebSearchResultRead(BaseModel):
    title: str
    url: str
    snippet: str
    published: str | None
    engine: str | None


class WebSearchResponseRead(BaseModel):
    provider: str
    query: str
    results: list[WebSearchResultRead]


class ScholarSourceRead(BaseModel):
    id: str
    name: str
    description: str
    default: bool


class ScholarSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    sources: list[str] | None = Field(default=None, max_length=5)
    limit: int = Field(default=10, ge=1, le=25)
    year_from: int | None = Field(default=None, ge=1000, le=2100)
    year_to: int | None = Field(default=None, ge=1000, le=2100)


class PaperRead(BaseModel):
    source: str
    title: str
    authors: list[str]
    year: int | None
    published: str | None
    venue: str | None
    doi: str | None
    url: str | None
    pdf_url: str | None
    abstract: str | None
    citation_count: int | None
    external_id: str | None


class ScholarSearchResponseRead(BaseModel):
    query: str
    papers: list[PaperRead]
    errors: dict[str, str]
