"""Web search providers. SearXNG is free and self-hosted; Tavily, Brave and Exa are paid APIs."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
from pydantic import SecretStr

from anvesha.config import Settings
from anvesha.errors import AppError, UpstreamError


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published: str | None = None
    engine: str | None = None


@dataclass(frozen=True)
class SearchAccess:
    provider: str
    api_base: str
    api_key: str | None = field(repr=False)
    source: Literal["workspace", "deployment"]


class SearchError(UpstreamError):
    code = "search_failed"


SearchFunction = Callable[
    [httpx2.AsyncClient, SearchAccess, str, int, Settings], Awaitable[list[SearchResult]]
]


@dataclass(frozen=True)
class SearchProviderSpec:
    id: str
    name: str
    requires_api_key: bool
    # Whether a workspace can add its own key (SearXNG is set up by the server operator).
    workspace_configurable: bool
    default_api_base: str | None
    settings_key_field: str | None
    settings_base_field: str | None
    search: SearchFunction
    free: bool = False
    docs_url: str = ""


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if value else ""


async def _send(
    http: httpx2.AsyncClient,
    provider: str,
    method: str,
    url: str,
    settings: Settings,
    **kwargs: Any,
) -> Any:
    headers = {"User-Agent": settings.api_user_agent, "Accept": "application/json"}
    headers.update(kwargs.pop("headers", {}))
    try:
        response = await http.request(
            method, url, headers=headers, timeout=settings.search_timeout_seconds, **kwargs
        )
    except httpx2.TimeoutException as exc:
        raise SearchError(f"{provider} timed out.", code="search_timeout", http_status=504) from exc
    except httpx2.HTTPError as exc:
        raise SearchError(
            f"{provider} is unreachable ({exc.__class__.__name__}).", code="search_unreachable"
        ) from exc
    if response.status_code in (401, 403):
        raise SearchError(
            f"{provider} rejected the API key (HTTP {response.status_code}).",
            code="search_auth_failed",
            retryable=False,
        )
    if response.status_code == 429:
        raise SearchError(
            f"{provider} rate limit reached; try again later.",
            code="search_rate_limited",
            http_status=429,
        )
    if response.status_code >= 300:
        # APIs don't redirect normal requests; a redirect usually means an error page.
        raise SearchError(
            f"{provider} returned HTTP {response.status_code}: {response.text[:300]}",
            retryable=response.status_code >= 500,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise SearchError(f"{provider} returned a response that isn't JSON.") from exc


async def search_searxng(
    http: httpx2.AsyncClient, access: SearchAccess, query: str, limit: int, settings: Settings
) -> list[SearchResult]:
    data = await _send(
        http,
        "SearXNG",
        "GET",
        f"{access.api_base}/search",
        settings,
        params={"q": query, "format": "json", "categories": "general", "safesearch": "0"},
    )
    if not isinstance(data, dict):
        raise SearchError("SearXNG returned an unexpected response.")
    return [
        SearchResult(
            title=_clean(item.get("title"), 500),
            url=item["url"],
            snippet=_clean(item.get("content"), 1000),
            published=_clean(item.get("publishedDate"), 32) or None,
            engine=_clean(item.get("engine"), 64) or None,
        )
        for item in data.get("results", [])
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ][:limit]


async def search_tavily(
    http: httpx2.AsyncClient, access: SearchAccess, query: str, limit: int, settings: Settings
) -> list[SearchResult]:
    data = await _send(
        http,
        "Tavily",
        "POST",
        f"{access.api_base}/search",
        settings,
        headers={"Authorization": f"Bearer {access.api_key}"},
        json={"query": query, "max_results": limit, "search_depth": "basic"},
    )
    return [
        SearchResult(
            title=_clean(item.get("title"), 500),
            url=item["url"],
            snippet=_clean(item.get("content"), 1000),
            published=_clean(item.get("published_date"), 32) or None,
            engine="tavily",
        )
        for item in (data.get("results", []) if isinstance(data, dict) else [])
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ][:limit]


async def search_brave(
    http: httpx2.AsyncClient, access: SearchAccess, query: str, limit: int, settings: Settings
) -> list[SearchResult]:
    data = await _send(
        http,
        "Brave Search",
        "GET",
        f"{access.api_base}/web/search",
        settings,
        headers={"X-Subscription-Token": access.api_key or ""},
        params={"q": query, "count": min(limit, 20)},
    )
    web = data.get("web", {}) if isinstance(data, dict) else {}
    return [
        SearchResult(
            title=_clean(item.get("title"), 500),
            url=item["url"],
            snippet=_clean(item.get("description"), 1000),
            published=_clean(item.get("page_age") or item.get("age"), 32) or None,
            engine="brave",
        )
        for item in (web.get("results", []) if isinstance(web, dict) else [])
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ][:limit]


async def search_exa(
    http: httpx2.AsyncClient, access: SearchAccess, query: str, limit: int, settings: Settings
) -> list[SearchResult]:
    data = await _send(
        http,
        "Exa",
        "POST",
        f"{access.api_base}/search",
        settings,
        headers={"x-api-key": access.api_key or ""},
        json={
            "query": query,
            "numResults": limit,
            "contents": {"text": {"maxCharacters": 1000}},
        },
    )
    return [
        SearchResult(
            title=_clean(item.get("title"), 500),
            url=item["url"],
            snippet=_clean(item.get("text") or item.get("summary"), 1000),
            published=_clean(item.get("publishedDate"), 32) or None,
            engine="exa",
        )
        for item in (data.get("results", []) if isinstance(data, dict) else [])
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ][:limit]


SEARCH_PROVIDERS: dict[str, SearchProviderSpec] = {
    spec.id: spec
    for spec in (
        SearchProviderSpec(
            id="searxng",
            name="SearXNG (self-hosted, free)",
            requires_api_key=False,
            workspace_configurable=False,
            default_api_base=None,
            settings_key_field=None,
            settings_base_field="searxng_url",
            search=search_searxng,
            free=True,
            docs_url="https://docs.searxng.org/",
        ),
        SearchProviderSpec(
            id="tavily",
            name="Tavily",
            requires_api_key=True,
            workspace_configurable=True,
            default_api_base="https://api.tavily.com",
            settings_key_field="tavily_api_key",
            settings_base_field=None,
            search=search_tavily,
            docs_url="https://app.tavily.com/",
        ),
        SearchProviderSpec(
            id="brave",
            name="Brave Search",
            requires_api_key=True,
            workspace_configurable=True,
            default_api_base="https://api.search.brave.com/res/v1",
            settings_key_field="brave_api_key",
            settings_base_field=None,
            search=search_brave,
            docs_url="https://api-dashboard.search.brave.com/",
        ),
        SearchProviderSpec(
            id="exa",
            name="Exa",
            requires_api_key=True,
            workspace_configurable=True,
            default_api_base="https://api.exa.ai",
            settings_key_field="exa_api_key",
            settings_base_field=None,
            search=search_exa,
            docs_url="https://dashboard.exa.ai/api-keys",
        ),
    )
}


def deployment_search_access(spec: SearchProviderSpec, settings: Settings) -> SearchAccess | None:
    key_value = getattr(settings, spec.settings_key_field) if spec.settings_key_field else None
    api_key = key_value.get_secret_value() if isinstance(key_value, SecretStr) else None
    base_value = getattr(settings, spec.settings_base_field) if spec.settings_base_field else None
    api_base = base_value or spec.default_api_base
    if (spec.requires_api_key and not api_key) or not api_base:
        return None
    return SearchAccess(spec.id, api_base.rstrip("/"), api_key, "deployment")


async def run_search(
    http: httpx2.AsyncClient,
    spec: SearchProviderSpec,
    access: SearchAccess,
    query: str,
    limit: int,
    settings: Settings,
) -> list[SearchResult]:
    try:
        async with asyncio.timeout(settings.search_timeout_seconds + 5):
            results = await spec.search(http, access, query, limit, settings)
    except TimeoutError as exc:
        raise SearchError(f"{spec.name} timed out.", code="search_timeout") from exc
    except AppError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SearchError(f"{spec.name} returned an unexpected response.") from exc
    # Drop duplicate URLs some engines return.
    seen: set[str] = set()
    unique = []
    for result in results:
        if result.url not in seen and result.url.startswith(("http://", "https://")):
            seen.add(result.url)
            unique.append(result)
    return unique
