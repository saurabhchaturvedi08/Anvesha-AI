"""Structured answers from any model: the model "submits" by calling a tool.

Tool calling is the one structured-output mechanism every supported provider shares.
Forcing a specific tool isn't (OpenAI-compatible servers differ), so instead a model
that answers in prose or with invalid arguments is told what went wrong and asked again.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from anvesha.agents.context import RunContext
from anvesha.llm.errors import LLMInvalidResponseError
from anvesha.llm.types import AgentRole, Message, ToolSpec
from anvesha.tools.base import tool_parameters_schema


@dataclass(frozen=True)
class SubmitTool[M: BaseModel]:
    name: str
    description: str
    model: type[M]

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, tool_parameters_schema(self.model))

    def parse(self, arguments: dict[str, object]) -> M:
        return self.model.model_validate(arguments)


def validation_problems(exc: ValidationError, limit: int = 6) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors()[:limit]
    )


async def structured_call[M: BaseModel](
    ctx: RunContext,
    *,
    role: AgentRole,
    messages: Sequence[Message],
    tool: SubmitTool[M],
    max_tokens: int = 4096,
    attempts: int = 3,
) -> M:
    history = list(messages)
    spec = tool.spec()
    for _ in range(attempts):
        completion = await ctx.complete(role, history, tools=[spec], max_tokens=max_tokens)
        history.append(Message.assistant(completion.text, completion.tool_calls))
        if not completion.tool_calls:
            history.append(
                Message.user(f"Submit your answer by calling the `{tool.name}` tool, not as text.")
            )
            continue
        result: M | None = None
        for call in completion.tool_calls:
            if call.name != tool.name or result is not None:
                history.append(
                    Message.tool_result(call.id, f"Only one `{tool.name}` call is expected.")
                )
                continue
            try:
                result = tool.parse(call.arguments)
            except ValidationError as exc:
                history.append(
                    Message.tool_result(
                        call.id,
                        f"Invalid arguments: {validation_problems(exc)}. Call `{tool.name}` "
                        "again with corrected arguments.",
                    )
                )
                continue
            history.append(Message.tool_result(call.id, "Received."))
        if result is not None:
            return result
    raise LLMInvalidResponseError(
        f"The {role} model did not return a valid `{tool.name}` call after {attempts} attempts."
    )


def as_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
