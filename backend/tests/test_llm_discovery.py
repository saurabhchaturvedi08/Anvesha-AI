from collections.abc import AsyncIterator

import httpx2
import pytest

from anvesha.llm.catalog import PROVIDERS
from anvesha.llm.discovery import check_credential
from tests.conftest import FakeProviderAPI


@pytest.fixture
async def fake() -> AsyncIterator[tuple[FakeProviderAPI, httpx2.AsyncClient]]:
    api = FakeProviderAPI()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(api.handler)) as http:
        yield api, http


async def test_valid_key_returns_sorted_models(
    fake: tuple[FakeProviderAPI, httpx2.AsyncClient],
) -> None:
    api, http = fake
    api.add("https://api.openai.com/v1/models", 200, {"data": [{"id": "b"}, {"id": "a"}]})
    check = await check_credential(http, PROVIDERS["openai"], "https://api.openai.com/v1", "sk-x")
    assert (check.status, check.models) == ("valid", ["a", "b"])
    assert api.requests[0].headers["authorization"] == "Bearer sk-x"


async def test_gemini_bad_key_400_is_invalid_and_prefix_is_stripped(
    fake: tuple[FakeProviderAPI, httpx2.AsyncClient],
) -> None:
    api, http = fake
    base = PROVIDERS["gemini"].default_api_base or ""
    api.add(f"{base}/models", 400, [{"error": {"message": "API key not valid."}}])
    check = await check_credential(http, PROVIDERS["gemini"], base, "bad")
    assert (check.status, check.message) == ("invalid", "API key not valid.")

    api.add(f"{base}/models", 200, {"data": [{"id": "models/gemini-flash"}]})
    check = await check_credential(http, PROVIDERS["gemini"], base, "good")
    assert check.models == ["gemini-flash"]


async def test_anthropic_sends_api_version_header(
    fake: tuple[FakeProviderAPI, httpx2.AsyncClient],
) -> None:
    api, http = fake
    api.add("https://api.anthropic.com/v1/models", 200, {"data": [{"id": "claude-x"}]})
    check = await check_credential(
        http, PROVIDERS["anthropic"], "https://api.anthropic.com", "sk-ant"
    )
    assert check.models == ["claude-x"]
    assert api.requests[0].headers["authorization"] == "Bearer sk-ant"
    assert api.requests[0].headers["anthropic-version"]


async def test_openrouter_checks_key_endpoint_then_lists_models(
    fake: tuple[FakeProviderAPI, httpx2.AsyncClient],
) -> None:
    api, http = fake
    base = "https://openrouter.ai/api/v1"
    api.add(f"{base}/key", 200, {"data": {"label": "mine"}})
    api.add(f"{base}/models", 200, {"data": [{"id": "anthropic/claude-x"}]})
    check = await check_credential(http, PROVIDERS["openrouter"], base, "sk-or")
    assert (check.status, check.models) == ("valid", ["anthropic/claude-x"])
    assert [str(r.url) for r in api.requests] == [f"{base}/key", f"{base}/models"]


async def test_server_errors_and_network_failures_are_unreachable(
    fake: tuple[FakeProviderAPI, httpx2.AsyncClient],
) -> None:
    api, http = fake
    api.add("https://api.openai.com/v1/models", 503, {"error": {"message": "overloaded"}})
    check = await check_credential(http, PROVIDERS["openai"], "https://api.openai.com/v1", "k")
    assert (check.status, check.message) == ("unreachable", "overloaded")

    check = await check_credential(http, PROVIDERS["ollama"], "http://nowhere:11434/v1", None)
    assert check.status == "unreachable"
