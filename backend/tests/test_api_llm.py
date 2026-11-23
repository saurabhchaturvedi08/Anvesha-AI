from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from anvesha.config import Settings
from anvesha.db.models import ProviderCredential
from tests.conftest import FakeAdapter, FakeProviderAPI, make_settings

OPENAI_MODELS = "https://api.openai.com/v1/models"
SECRET = "sk-proj-THIS-IS-A-SECRET-1234"


def _provider(providers: list[dict[str, Any]], provider_id: str) -> dict[str, Any]:
    return next(p for p in providers if p["id"] == provider_id)


async def test_catalog_lists_every_provider_unconfigured(client: AsyncClient) -> None:
    providers = (await client.get("/api/v1/providers")).json()
    assert {p["id"] for p in providers} >= {"openai", "anthropic", "gemini", "openrouter", "ollama"}
    assert not any(p["configured"] for p in providers)


async def test_saving_a_valid_key_encrypts_it_and_never_returns_it(
    app: FastAPI, client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    provider_api.add(OPENAI_MODELS, 200, {"data": [{"id": "gpt-x"}]})

    saved = await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    assert saved.status_code == 200
    body = saved.json()
    assert (body["status"], body["source"], body["key_hint"]) == ("valid", "workspace", "…1234")
    assert body["models"] == ["gpt-x"]
    assert SECRET not in saved.text

    listed = await client.get("/api/v1/providers")
    assert SECRET not in listed.text
    assert _provider(listed.json(), "openai")["configured"] is True

    async with app.state.db.session() as session:
        credential = await session.scalar(select(ProviderCredential))
    assert credential is not None
    assert credential.encrypted_api_key and SECRET not in credential.encrypted_api_key


async def test_rejected_key_is_not_stored(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    provider_api.add(OPENAI_MODELS, 401, {"error": {"message": "Incorrect API key provided"}})
    response = await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    assert response.status_code == 422
    assert "Incorrect API key" in response.json()["detail"]
    assert (
        _provider((await client.get("/api/v1/providers")).json(), "openai")["configured"] is False
    )


async def test_unreachable_provider_stores_key_as_unverified(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    provider_api.add(OPENAI_MODELS, 503, {"error": {"message": "overloaded"}})
    response = await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["last_error"]) == (
        "unverified",
        "overloaded",
    )


async def test_credential_input_validation(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    missing_key = await client.put("/api/v1/providers/openai/credential", json={})
    custom_base = await client.put(
        "/api/v1/providers/openai/credential",
        json={"api_key": SECRET, "api_base": "https://evil.example/v1"},
    )
    no_base = await client.put("/api/v1/providers/openai_compatible/credential", json={})
    unknown = await client.put("/api/v1/providers/nope/credential", json={"api_key": SECRET})
    assert [missing_key.status_code, custom_base.status_code, no_base.status_code] == [422] * 3
    assert unknown.status_code == 404
    assert provider_api.requests == []  # nothing was sent to any provider


async def test_ollama_needs_no_key(client: AsyncClient, provider_api: FakeProviderAPI) -> None:
    provider_api.add("http://localhost:11434/v1/models", 200, {"data": [{"id": "llama3.2"}]})
    response = await client.put("/api/v1/providers/ollama/credential", json={})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["key_hint"]) == ("valid", None)


async def test_delete_credential(client: AsyncClient, provider_api: FakeProviderAPI) -> None:
    provider_api.add(OPENAI_MODELS, 200, {"data": []})
    await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    assert (await client.delete("/api/v1/providers/openai/credential")).status_code == 204
    assert (await client.delete("/api/v1/providers/openai/credential")).status_code == 404


async def test_list_models_reverifies_key(
    app: FastAPI, client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    assert (await client.get("/api/v1/providers/openai/models")).status_code == 409

    provider_api.add(OPENAI_MODELS, 200, {"data": [{"id": "gpt-x"}]})
    await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    assert (await client.get("/api/v1/providers/openai/models")).json() == ["gpt-x"]
    assert provider_api.requests[-1].headers["authorization"] == f"Bearer {SECRET}"

    provider_api.add(OPENAI_MODELS, 401, {"error": {"message": "revoked"}})
    assert (await client.get("/api/v1/providers/openai/models")).status_code == 502
    status_now = _provider((await client.get("/api/v1/providers")).json(), "openai")["status"]
    assert status_now == "invalid"


async def test_model_preferences_require_a_configured_provider(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    pick = {"provider": "openai", "model": "gpt-x"}
    assert (await client.put("/api/v1/model-preferences/lead", json=pick)).status_code == 409
    bad_role = await client.put("/api/v1/model-preferences/boss", json=pick)
    assert bad_role.status_code == 422

    provider_api.add(OPENAI_MODELS, 200, {"data": []})
    await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    assert (await client.put("/api/v1/model-preferences/lead", json=pick)).status_code == 200

    preferences = (await client.get("/api/v1/model-preferences")).json()
    assert preferences[0] == {"role": "lead", "provider": "openai", "model": "gpt-x"}
    assert preferences[1] == {"role": "researcher", "provider": None, "model": None}

    assert (await client.delete("/api/v1/model-preferences/lead")).status_code == 204
    assert (await client.get("/api/v1/model-preferences")).json()[0]["model"] is None


async def test_embedding_role_needs_a_provider_with_embeddings(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    roles = [p["role"] for p in (await client.get("/api/v1/model-preferences")).json()]
    assert roles == ["lead", "researcher", "verifier", "writer", "embedding"]

    provider_api.add("https://api.anthropic.com/v1/models", 200, {"data": []})
    provider_api.add(OPENAI_MODELS, 200, {"data": []})
    await client.put("/api/v1/providers/anthropic/credential", json={"api_key": SECRET})
    await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})

    claude = {"provider": "anthropic", "model": "claude-haiku-4-5"}
    rejected = await client.put("/api/v1/model-preferences/embedding", json=claude)
    assert rejected.status_code == 422 and "embeddings" in rejected.json()["detail"]
    openai = {"provider": "openai", "model": "text-embedding-3-small"}
    assert (await client.put("/api/v1/model-preferences/embedding", json=openai)).status_code == 200


async def test_llm_test_endpoint_and_usage_summary(
    client: AsyncClient, provider_api: FakeProviderAPI, fake_llm: FakeAdapter
) -> None:
    not_configured = await client.post("/api/v1/llm/test", json={"role": "lead"})
    assert not_configured.status_code == 409
    assert not_configured.json()["code"] == "model_not_configured"

    provider_api.add(OPENAI_MODELS, 200, {"data": []})
    await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
    await client.put(
        "/api/v1/model-preferences/lead", json={"provider": "openai", "model": "gpt-4o-mini"}
    )

    response = await client.post("/api/v1/llm/test", json={"role": "lead", "prompt": "ping"})
    assert response.status_code == 200
    body = response.json()
    assert (body["provider"], body["model"], body["text"]) == (
        "openai",
        "gpt-4o-mini",
        "fake reply",
    )
    assert float(body["cost_usd"]) > 0
    assert fake_llm.calls[0]["messages"][0].content == "ping"

    usage = (await client.get("/api/v1/usage")).json()
    assert (usage["calls"], usage["input_tokens"], usage["output_tokens"]) == (1, 1000, 500)
    assert usage["by_model"][0]["model"] == "gpt-4o-mini"
    assert float(usage["cost_usd"]) == float(body["cost_usd"])


class TestWithoutSecretKey:
    @pytest.fixture
    def test_settings(self, database_url: str) -> Settings:
        return make_settings(database_url=database_url, secret_key=None, secret_key_file=None)

    async def test_keys_cannot_be_stored(
        self, client: AsyncClient, provider_api: FakeProviderAPI
    ) -> None:
        response = await client.put("/api/v1/providers/openai/credential", json={"api_key": SECRET})
        assert response.status_code == 503
        assert provider_api.requests == []


class TestPrivateEndpointsBlocked:
    @pytest.fixture
    def test_settings(self, database_url: str) -> Settings:
        return make_settings(database_url=database_url, allow_private_network_endpoints=False)

    async def test_ollama_on_localhost_is_refused(
        self, client: AsyncClient, provider_api: FakeProviderAPI
    ) -> None:
        response = await client.put(
            "/api/v1/providers/ollama/credential", json={"api_base": "http://127.0.0.1:11434/v1"}
        )
        assert response.status_code == 422
        assert "non-public" in response.json()["detail"]
        assert provider_api.requests == []
