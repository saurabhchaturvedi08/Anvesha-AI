"""Provider-neutral message and completion types. Adapters translate these per provider."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

AgentRole = Literal["lead", "researcher", "verifier", "writer"]
AGENT_ROLES: tuple[AgentRole, ...] = ("lead", "researcher", "verifier", "writer")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the tool's arguments


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # set on role="tool" messages

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls("system", content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls("user", content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: tuple[ToolCall, ...] = ()) -> "Message":
        return cls("assistant", content, tool_calls)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> "Message":
        return cls("tool", content, tool_call_id=tool_call_id)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: TokenUsage
    provider: str
    model: str
    finish_reason: str | None
    cost_usd: Decimal | None = None  # None when the price of this model is unknown


@dataclass(frozen=True)
class ProviderAccess:
    """Everything needed to call one provider. Never log or serialize the key."""

    provider: str
    api_base: str
    api_key: str | None = field(repr=False)
    source: Literal["workspace", "deployment"]


@dataclass(frozen=True)
class ModelRoute:
    access: ProviderAccess
    model: str
