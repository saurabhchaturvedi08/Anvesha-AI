"""Adapters run through the real provider SDKs, with HTTP intercepted by a mock transport."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest

from anvesha.llm.adapters import AnthropicAdapter, OpenAICompatibleAdapter
from anvesha.llm.errors import LLMAuthError, LLMInvalidResponseError, LLMRateLimitError
from anvesha.llm.types import Message, ModelRoute, ProviderAccess, ToolCall, ToolSpec

SEARCH_TOOL = ToolSpec(
    name="web_search",
    description="Search the web",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)
CONVERSATION = [
    Message.system("You are a researcher."),
    Message.user("Find X."),
    Message.assistant(
        tool_calls=(ToolCall(id="call_1", name="web_search", arguments={"query": "X"}),)
    ),
    Message.tool_result("call_1", "X is 42."),
]


def route(provider: str, base: str) -> ModelRoute:
    access = ProviderAccess(provider=provider, api_base=base, api_key="sk-test", source="workspace")
    return ModelRoute(access=access, model="test-model")


class Recorder:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)

    @property
    def sent(self) -> dict[str, Any]:
        return dict(json.loads(self.requests[-1].content))


@pytest.fixture
async def http_for() -> AsyncIterator[Callable[[Recorder], httpx2.AsyncClient]]:
    clients: list[httpx2.AsyncClient] = []

    def make(recorder: Recorder) -> httpx2.AsyncClient:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


OPENAI_TOOL_RESPONSE = {
    "id": "c1",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model-2026",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query": "Y"}'},
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


async def test_openai_adapter_translates_request_and_response(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    recorder = Recorder(200, OPENAI_TOOL_RESPONSE)
    adapter = OpenAICompatibleAdapter(http_for(recorder), max_retries=0)

    completion = await adapter.complete(
        route("openai", "https://api.openai.com/v1"),
        CONVERSATION,
        tools=[SEARCH_TOOL],
        max_tokens=100,
        request_timeout=5,
    )

    assert str(recorder.requests[-1].url) == "https://api.openai.com/v1/chat/completions"
    assert recorder.requests[-1].headers["authorization"] == "Bearer sk-test"
    sent = recorder.sent
    assert sent["max_completion_tokens"] == 100 and "max_tokens" not in sent
    assert sent["tools"][0]["function"]["name"] == "web_search"
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool"]
    assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"query": "X"}'
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "X is 42."}

    assert completion.tool_calls == (
        ToolCall(id="call_2", name="web_search", arguments={"query": "Y"}),
    )
    assert (completion.usage.input_tokens, completion.usage.output_tokens) == (10, 5)
    assert (completion.provider, completion.model) == ("openai", "test-model-2026")
    assert completion.finish_reason == "tool_calls"


async def test_compatible_providers_get_max_tokens(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    recorder = Recorder(200, OPENAI_TOOL_RESPONSE)
    adapter = OpenAICompatibleAdapter(http_for(recorder), max_retries=0)
    await adapter.complete(
        route("groq", "https://api.groq.com/openai/v1"),
        [Message.user("hi")],
        max_tokens=50,
        request_timeout=5,
    )
    assert recorder.sent["max_tokens"] == 50 and "max_completion_tokens" not in recorder.sent


async def test_openai_adapter_rejects_malformed_tool_arguments(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    body = json.loads(json.dumps(OPENAI_TOOL_RESPONSE))
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
    adapter = OpenAICompatibleAdapter(http_for(Recorder(200, body)), max_retries=0)
    with pytest.raises(LLMInvalidResponseError):
        await adapter.complete(
            route("openai", "https://x/v1"), [Message.user("hi")], request_timeout=5
        )


@pytest.mark.parametrize(("status", "error"), [(401, LLMAuthError), (429, LLMRateLimitError)])
async def test_openai_adapter_maps_errors(
    http_for: Callable[[Recorder], httpx2.AsyncClient], status: int, error: type[Exception]
) -> None:
    body = {"error": {"message": "nope", "type": "error"}}
    adapter = OpenAICompatibleAdapter(http_for(Recorder(status, body)), max_retries=0)
    with pytest.raises(error):
        await adapter.complete(
            route("openai", "https://x/v1"), [Message.user("hi")], request_timeout=5
        )


ANTHROPIC_RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-test",
    "content": [
        {"type": "text", "text": "Searching."},
        {"type": "tool_use", "id": "tu_2", "name": "web_search", "input": {"query": "Y"}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 7},
}


async def test_anthropic_adapter_translates_request_and_response(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    recorder = Recorder(200, ANTHROPIC_RESPONSE)
    adapter = AnthropicAdapter(http_for(recorder), max_retries=0)

    completion = await adapter.complete(
        route("anthropic", "https://api.anthropic.com"),
        [*CONVERSATION, Message.tool_result("call_9", "second result")],
        tools=[SEARCH_TOOL],
        request_timeout=5,
    )

    request = recorder.requests[-1]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["authorization"] == "Bearer sk-test"
    sent = recorder.sent
    assert sent["system"] == "You are a researcher."
    assert sent["max_tokens"] == 4096
    assert sent["tools"][0]["input_schema"] == SEARCH_TOOL.parameters
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
    assert sent["messages"][1]["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "web_search", "input": {"query": "X"}}
    ]
    # Consecutive tool results merge into one user turn.
    assert [block["tool_use_id"] for block in sent["messages"][2]["content"]] == [
        "call_1",
        "call_9",
    ]

    assert completion.text == "Searching."
    assert completion.tool_calls == (
        ToolCall(id="tu_2", name="web_search", arguments={"query": "Y"}),
    )
    assert (completion.usage.input_tokens, completion.usage.output_tokens) == (12, 7)
    assert completion.finish_reason == "tool_use"


async def test_anthropic_adapter_maps_auth_error(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    body = {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
    adapter = AnthropicAdapter(http_for(Recorder(401, body)), max_retries=0)
    with pytest.raises(LLMAuthError):
        await adapter.complete(
            route("anthropic", "https://api.anthropic.com"), [Message.user("hi")], request_timeout=5
        )


async def test_openai_compatible_embeddings(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    recorder = Recorder(
        200,
        {
            "object": "list",
            "model": "test-model",
            # Returned out of order on purpose: the adapter must sort by index.
            "data": [
                {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
            ],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        },
    )
    adapter = OpenAICompatibleAdapter(http_for(recorder), max_retries=0)
    result = await adapter.embed(
        route("openai", "https://api.openai.com/v1"), ["first", "second"], request_timeout=5
    )
    assert result.vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert result.usage.input_tokens == 7
    assert recorder.sent["input"] == ["first", "second"]
    assert str(recorder.requests[-1].url) == "https://api.openai.com/v1/embeddings"


async def test_embeddings_with_the_wrong_count_are_rejected(
    http_for: Callable[[Recorder], httpx2.AsyncClient],
) -> None:
    recorder = Recorder(
        200,
        {
            "object": "list",
            "model": "m",
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )
    adapter = OpenAICompatibleAdapter(http_for(recorder), max_retries=0)
    with pytest.raises(LLMInvalidResponseError, match="expected 2"):
        await adapter.embed(route("ollama", "http://ollama/v1"), ["a", "b"], request_timeout=5)
