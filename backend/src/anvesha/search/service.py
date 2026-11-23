"""Choosing a search provider for a workspace and running searches through it."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx2
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.config import Settings
from anvesha.crypto import DecryptionError, SecretBox
from anvesha.db.models import ProviderCredential
from anvesha.db.session import Database
from anvesha.errors import InvalidInputError, NotConfiguredError
from anvesha.llm.credentials import credential_aad, get_credential
from anvesha.search.providers import (
    SEARCH_PROVIDERS,
    SearchAccess,
    SearchError,
    SearchProviderSpec,
    SearchResult,
    deployment_search_access,
    run_search,
)


@dataclass(frozen=True)
class WebSearchResponse:
    provider: str
    query: str
    results: list[SearchResult]


def get_search_provider(provider_id: str) -> SearchProviderSpec:
    spec = SEARCH_PROVIDERS.get(provider_id)
    if spec is None:
        raise InvalidInputError(
            f"Unknown search provider {provider_id!r}. "
            f"Choose one of: {', '.join(SEARCH_PROVIDERS)}.",
            code="unknown_search_provider",
        )
    return spec


async def resolve_search_access(
    session: AsyncSession,
    settings: Settings,
    vault: SecretBox | None,
    workspace_id: uuid.UUID,
    spec: SearchProviderSpec,
) -> SearchAccess | None:
    """The workspace's own key if it added one, else the server's, else None."""
    if spec.workspace_configurable:
        credential = await get_credential(session, workspace_id, spec.id)
        if credential is not None and credential.encrypted_api_key is not None:
            if vault is None:
                raise NotConfiguredError(
                    "Stored keys can't be decrypted because no secret key is configured."
                )
            try:
                api_key = vault.decrypt(
                    credential.encrypted_api_key, credential_aad(workspace_id, spec.id)
                )
            except DecryptionError as exc:
                raise NotConfiguredError(
                    f"The stored {spec.name} key can't be decrypted (the server's secret key "
                    "changed). Enter the key again."
                ) from exc
            assert spec.default_api_base is not None
            return SearchAccess(spec.id, spec.default_api_base, api_key, "workspace")
    return deployment_search_access(spec, settings)


async def choose_search_access(
    session: AsyncSession,
    settings: Settings,
    vault: SecretBox | None,
    workspace_id: uuid.UUID,
    provider: str | None,
) -> tuple[SearchProviderSpec, SearchAccess]:
    """The named provider, or the default, or the first one that's configured."""
    if provider is not None:
        spec = get_search_provider(provider)
        access = await resolve_search_access(session, settings, vault, workspace_id, spec)
        if access is None:
            raise NotConfiguredError(
                f"{spec.name} isn't configured.", code="search_provider_not_configured"
            )
        return spec, access

    order = list(SEARCH_PROVIDERS)
    if settings.default_search_provider in SEARCH_PROVIDERS:
        order.remove(settings.default_search_provider)
        order.insert(0, settings.default_search_provider)
    # Prefer a key the workspace added itself over server-wide defaults.
    fallback: tuple[SearchProviderSpec, SearchAccess] | None = None
    for provider_id in order:
        spec = SEARCH_PROVIDERS[provider_id]
        access = await resolve_search_access(session, settings, vault, workspace_id, spec)
        if access is None:
            continue
        if access.source == "workspace" or provider_id == settings.default_search_provider:
            return spec, access
        fallback = fallback or (spec, access)
    if fallback is not None:
        return fallback
    raise NotConfiguredError(
        "No web search provider is configured. Run SearXNG (included in docker compose and "
        "set ANVESHA_SEARXNG_URL) or add a Tavily, Brave or Exa key.",
        code="search_provider_not_configured",
    )


async def web_search(
    *,
    db: Database,
    http: httpx2.AsyncClient,
    settings: Settings,
    vault: SecretBox | None,
    workspace_id: uuid.UUID,
    query: str,
    limit: int = 8,
    provider: str | None = None,
) -> WebSearchResponse:
    async with db.session() as session:
        spec, access = await choose_search_access(session, settings, vault, workspace_id, provider)
    try:
        results = await run_search(http, spec, access, query, limit, settings)
    except SearchError as exc:
        if exc.code == "search_auth_failed" and access.source == "workspace":
            async with db.session() as session:
                await session.execute(
                    update(ProviderCredential)
                    .where(
                        ProviderCredential.workspace_id == workspace_id,
                        ProviderCredential.provider == spec.id,
                    )
                    .values(
                        status="invalid", last_error=str(exc), last_checked_at=datetime.now(UTC)
                    )
                )
                await session.commit()
        raise
    return WebSearchResponse(provider=spec.id, query=query, results=results)
