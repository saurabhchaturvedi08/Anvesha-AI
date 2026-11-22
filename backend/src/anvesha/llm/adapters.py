"""Chat adapters: translate provider-neutral messages to each provider's API and back."""

import json
import math
from collections.abc import Sequence
from typing import Any, Protocol, cast

import anthropic
import httpx2
import openai

from anvesha.llm.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConnectionError,
    LLMError,
    LLMInvalidResponseError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from anvesha.llm.types import (
    Completion,
    Embeddings,
    Message,
    ModelRoute,
    TokenUsage,
    ToolCall,
    ToolSpec,
)

DEFAULT_MAX_TOKENS = 4096


class ChatAdapter(Protocol):
    async def complete(
        self,
        route: ModelRoute,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
        request_timeout: float,
    ) -> Completion: ...


class EmbeddingAdapter(Protocol):
    async def embed(
        self, route: ModelRoute, texts: Sequence[str], *, request_timeout: float
    ) -> Embeddings: ...


def _map_sdk_error(exc: Exception, provider: str) -> LLMError:
    """Both SDKs share the same exception names; check the more specific ones first."""
    detail = f"{provider}: {exc}"[:1000]
    for module in (openai, anthropic):
        if isinstance(exc, module.AuthenticationError | module.PermissionDeniedError):
            return LLMAuthError(detail)
        if isinstance(exc, module.RateLimitError):
            return LLMRateLimitError(detail)
        if isinstance(exc, module.APITimeoutError):
            return LLMTimeoutError(detail)
        if isinstance(exc, module.APIConnectionError):
            return LLMConnectionError(detail)
        if isinstance(exc, module.BadRequestError | module.NotFoundError):
            return LLMBadRequestError(detail)
    return LLMProviderError(detail)


class OpenAICompatibleAdapter:
    """OpenAI, Gemini, OpenRouter, Groq, Mistral, Ollama, and custom compatible servers."""

    def __init__(self, http_client: httpx2.AsyncClient, max_retries: int = 2) -> None:
        self._http = http_client
        self._max_retries = max_retries

    def _client(self, route: ModelRoute) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            # Local servers accept any key, but the SDK requires a non-empty one.
            api_key=route.access.api_key or "not-needed",
            base_url=route.access.api_base,
            max_retries=self._max_retries,
            http_client=self._http,
        )

    async def embed(
        self, route: ModelRoute, texts: Sequence[str], *, request_timeout: float
    ) -> Embeddings:
        provider = route.access.provider
        try:
            response = await self._client(route).embeddings.create(
                model=route.model,
                input=list(texts),
                encoding_format="float",
                timeout=request_timeout,
            )
        except openai.OpenAIError as exc:
            raise _map_sdk_error(exc, provider) from exc

        rows = sorted(response.data, key=lambda row: row.index)
        vectors = [list(row.embedding) for row in rows]
        if len(vectors) != len(texts):
            raise LLMInvalidResponseError(
                f"{provider}: expected {len(texts)} embeddings, got {len(vectors)}"
            )
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) > 1 or 0 in dimensions:
            raise LLMInvalidResponseError(f"{provider}: embeddings have inconsistent sizes")
        if not all(math.isfinite(value) for vector in vectors for value in vector):
            raise LLMInvalidResponseError(f"{provider}: embeddings contain non-finite values")
        usage = response.usage
        return Embeddings(
            vectors=[[float(value) for value in vector] for vector in vectors],
            usage=TokenUsage(input_tokens=usage.prompt_tokens if usage else 0),
            provider=provider,
            model=response.model or route.model,
        )

    async def complete(
        self,
        route: ModelRoute,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
        request_timeout: float,
    ) -> Completion:
        access = route.access
        client = self._client(route)
        params: dict[str, Any] = {
            "model": route.model,
            "messages": [_to_openai_message(message) for message in messages],
            "timeout": request_timeout,
        }
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if max_tokens is not None:
            # OpenAI's newer models only accept max_completion_tokens; most compatible
            # servers still only understand max_tokens.
            key = "max_completion_tokens" if access.provider == "openai" else "max_tokens"
            params[key] = max_tokens
        if temperature is not None:
            params["temperature"] = temperature

        try:
            response = await client.chat.completions.create(**params)
        except openai.OpenAIError as exc:
            raise _map_sdk_error(exc, access.provider) from exc

        if not response.choices:
            raise LLMInvalidResponseError(f"{access.provider}: response contained no choices")
        choice = response.choices[0]
        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments, access.provider),
            )
            for call in (choice.message.tool_calls or [])
            if call.type == "function"
        )
        usage = response.usage
        return Completion(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
            provider=access.provider,
            model=response.model or route.model,
            finish_reason=choice.finish_reason,
        )


def _to_openai_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ],
        }
    return {"role": message.role, "content": message.content}


def _parse_arguments(raw: str | None, provider: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise LLMInvalidResponseError(f"{provider}: tool call arguments are not JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMInvalidResponseError(f"{provider}: tool call arguments are not an object")
    return parsed


class AnthropicAdapter:
    def __init__(self, http_client: httpx2.AsyncClient, max_retries: int = 2) -> None:
        self._http = http_client
        self._max_retries = max_retries

    async def complete(
        self,
        route: ModelRoute,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,  # not supported by current Claude models
        request_timeout: float,
    ) -> Completion:
        access = route.access
        client = anthropic.AsyncAnthropic(
            api_key=access.api_key,
            base_url=access.api_base,
            max_retries=self._max_retries,
            http_client=self._http,
        )
        system = "\n\n".join(m.content for m in messages if m.role == "system" and m.content)
        params: dict[str, Any] = {
            "model": route.model,
            "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
            "messages": _to_anthropic_messages(messages),
            "timeout": request_timeout,
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]

        try:
            response = await client.messages.create(**params)
        except anthropic.AnthropicError as exc:
            raise _map_sdk_error(exc, access.provider) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id, name=block.name, arguments=cast(dict[str, Any], block.input)
                    )
                )
        return Completion(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            provider=access.provider,
            model=response.model,
            finish_reason=response.stop_reason,
        )


def _to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Anthropic wants alternating user/assistant turns made of content blocks.

    Tool results travel as user-turn blocks, and consecutive same-role turns are merged.
    """
    turns: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            role = "user"
            blocks: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            ]
        else:
            role = message.role
            blocks = [{"type": "text", "text": message.content}] if message.content else []
            blocks += [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            ]
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    return turns
