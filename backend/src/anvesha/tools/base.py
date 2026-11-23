"""The tool registry: one definition per tool, used by the agents and (later) the MCP server.

A tool never raises into an agent loop. Bad arguments, failed fetches, and unexpected
bugs all come back as a ToolResult with ``is_error`` set, so the agent can adapt.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2
from pydantic import BaseModel, ValidationError

from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.session import Database
from anvesha.errors import AppError
from anvesha.llm.gateway import LLMGateway
from anvesha.llm.types import ToolSpec
from anvesha.sources.service import AddedBy, SourceService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """What a tool call may touch: one workspace's project, plus shared services."""

    workspace_id: uuid.UUID
    project_id: uuid.UUID
    db: Database
    settings: Settings
    http: httpx2.AsyncClient
    vault: SecretBox | None
    gateway: LLMGateway
    sources: SourceService
    added_by: AddedBy = "agent"


@dataclass(frozen=True)
class ToolResult:
    content: str  # what the model reads
    data: dict[str, Any] = field(default_factory=dict)  # structured, for APIs and the UI
    is_error: bool = False
    error_code: str | None = None
    retryable: bool = False

    @classmethod
    def error(cls, message: str, code: str, *, retryable: bool = False) -> "ToolResult":
        return cls(
            content=f"Error ({code}): {message}",
            data={"error": message, "code": code},
            is_error=True,
            error_code=code,
            retryable=retryable,
        )


Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler

    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        """Specs for the model, optionally limited to a subset (least privilege per agent)."""
        selected = self._tools.values() if names is None else (self._tools[n] for n in names)
        return [tool.spec() for tool in selected]

    async def invoke(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.error(
                f"There is no tool named {name!r}. Available: {', '.join(self._tools)}.",
                "unknown_tool",
            )
        try:
            args = tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in error['loc']) or 'arguments'}: {error['msg']}"
                for error in exc.errors()[:5]
            )
            return ToolResult.error(f"Invalid arguments. {problems}", "invalid_arguments")
        try:
            return await tool.handler(ctx, args)
        except AppError as exc:
            return ToolResult.error(str(exc), exc.code, retryable=exc.retryable)
        except Exception:
            logger.exception("Tool %s failed", name)
            return ToolResult.error("The tool failed unexpectedly.", "tool_failed")


def untrusted(label: str, body: str) -> str:
    """Mark text from the outside world so agents treat it as data, never as instructions."""
    safe = body.replace("</untrusted_content", "<\\/untrusted_content")
    return f'<untrusted_content source="{label}">\n{safe}\n</untrusted_content>'
