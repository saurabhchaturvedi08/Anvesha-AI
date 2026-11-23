from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from anvesha.config import Settings
from anvesha.db.models import ProviderCredential
from tests.conftest import FakeProviderAPI, make_settings

SEARXNG = "http://searxng:8080/search"
TAVILY = "https://api.tavily.com/search"
SECRET = "tvly-THIS-IS-SECRET-9876"


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return make_settings(database_url=database_url, searxng_url="http://searxng:8080")


def _provider(providers: list[dict[str, Any]], provider_id: str) -> dict[str, Any]:
    return next(p for p in providers if p["id"] == provider_id)


async def test_searxng_from_settings_is_the_default(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    providers = (await client.get("/api/v1/search/providers")).json()
    searxng = _provider(providers, "searxng")
    assert (searxng["configured"], searxng["source"], searxng["is_default"]) == (
        True,
        "deployment",
        True,
    )
    assert _provider(providers, "tavily")["configured"] is False

    provider_api.add(SEARXNG, 200, {"results": [{"title": "A", "url": "https://a.example"}]})
    response = await client.post("/api/v1/search/web", json={"query": "solar"})
    assert response.status_code == 200
    assert response.json()["provider"] == "searxng"
    assert response.json()["results"][0]["url"] == "https://a.example"


async def test_workspace_key_is_validated_encrypted_and_preferred(
    app: FastAPI, client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    provider_api.add(TAVILY, 401, {"detail": "bad key"})
    rejected = await client.put(
        "/api/v1/search/providers/tavily/credential", json={"api_key": "wrong"}
    )
    assert rejected.status_code == 422

    provider_api.add(TAVILY, 200, {"results": [{"title": "T", "url": "https://t.example"}]})
    saved = await client.put("/api/v1/search/providers/tavily/credential", json={"api_key": SECRET})
    assert saved.status_code == 200
    assert (saved.json()["status"], saved.json()["key_hint"]) == ("valid", "…9876")
    assert SECRET not in saved.text
    async with app.state.db.session() as session:
        credential = await session.scalar(select(ProviderCredential))
    assert credential is not None and SECRET not in (credential.encrypted_api_key or "")

    # The workspace's own key now wins over the server's SearXNG.
    response = await client.post("/api/v1/search/web", json={"query": "solar"})
    assert response.json()["provider"] == "tavily"
    assert provider_api.requests[-1].headers["authorization"] == f"Bearer {SECRET}"
    explicit = await client.post("/api/v1/search/web", json={"query": "q", "provider": "searxng"})
    assert explicit.status_code in (200, 502)  # routed to SearXNG (no fake route here)

    # A key that stops working is marked invalid.
    provider_api.add(TAVILY, 403, {})
    failed = await client.post("/api/v1/search/web", json={"query": "solar"})
    assert (failed.status_code, failed.json()["code"]) == (502, "search_auth_failed")
    providers = (await client.get("/api/v1/search/providers")).json()
    assert _provider(providers, "tavily")["status"] == "invalid"

    assert (await client.delete("/api/v1/search/providers/tavily/credential")).status_code == 204
    assert (await client.delete("/api/v1/search/providers/tavily/credential")).status_code == 404


async def test_search_provider_errors(client: AsyncClient) -> None:
    unknown = await client.post("/api/v1/search/web", json={"query": "q", "provider": "bing"})
    assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_search_provider")
    missing = await client.post("/api/v1/search/web", json={"query": "q", "provider": "exa"})
    assert (missing.status_code, missing.json()["code"]) == (409, "search_provider_not_configured")
    operator_only = await client.put(
        "/api/v1/search/providers/searxng/credential", json={"api_key": "x"}
    )
    assert operator_only.status_code == 422


async def test_nothing_configured_explains_how_to_fix_it(app: FastAPI, client: AsyncClient) -> None:
    app.state.settings = make_settings()  # no SearXNG
    response = await client.post("/api/v1/search/web", json={"query": "q"})
    assert response.status_code == 409 and "SearXNG" in response.json()["detail"]


async def test_scholarly_search_endpoint(
    client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    sources = (await client.get("/api/v1/search/scholarly/sources")).json()
    assert {s["id"] for s in sources} == {
        "openalex",
        "semantic_scholar",
        "arxiv",
        "crossref",
        "pubmed",
    }

    provider_api.add(
        "https://api.openalex.org/works",
        200,
        {"results": [{"id": "https://openalex.org/W1", "display_name": "Paper one"}]},
    )
    response = await client.post(
        "/api/v1/search/scholarly", json={"query": "solar", "sources": ["openalex", "crossref"]}
    )
    body = response.json()
    assert [p["title"] for p in body["papers"]] == ["Paper one"]
    assert set(body["errors"]) == {"crossref"}

    bad = await client.post("/api/v1/search/scholarly", json={"query": "q", "sources": ["nope"]})
    assert bad.status_code == 422
