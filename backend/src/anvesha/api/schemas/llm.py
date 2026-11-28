from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from anvesha.llm.types import AgentRole, ModelRole


class ProviderRead(BaseModel):
    id: str
    name: str
    requires_api_key: bool
    custom_api_base: bool
    default_api_base: str | None
    docs_url: str
    configured: bool
    # "workspace": your own key. "deployment": a key set by whoever runs this server.
    source: Literal["workspace", "deployment"] | None
    status: Literal["valid", "invalid", "unverified"] | None
    key_hint: str | None
    api_base: str | None
    last_checked_at: datetime | None
    last_error: str | None


class CredentialWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    api_key: SecretStr | None = Field(default=None, max_length=4096)
    api_base: str | None = Field(default=None, max_length=2048)


class CredentialSaved(ProviderRead):
    models: list[str]


class OAuthStartRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    callback_url: str = Field(
        min_length=1,
        max_length=2048,
        description="Where the provider sends the user back (a page of the web app).",
    )


class OAuthStartResponse(BaseModel):
    authorization_url: str = Field(description="Send the user here.")
    state: str = Field(description="Keep this (e.g. in session storage) for the callback.")


class OAuthComplete(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=1, max_length=4000)


class ModelPreferenceWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=255)


class ModelPreferenceRead(BaseModel):
    role: ModelRole
    provider: str | None
    model: str | None


class UsageByModel(BaseModel):
    provider: str
    model: str
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    unpriced_calls: int


class UsageSummary(BaseModel):
    since: datetime
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    unpriced_calls: int
    by_model: list[UsageByModel]


class LLMTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole = "lead"
    prompt: str = Field(
        default="Reply with one short sentence confirming you can read this.",
        min_length=1,
        max_length=4000,
    )
    max_tokens: int = Field(default=256, ge=1, le=2048)


class LLMTestResponse(BaseModel):
    provider: str
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
