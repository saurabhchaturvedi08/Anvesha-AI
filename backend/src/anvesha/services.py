"""The long-lived services one process needs, built the same way for the API and worker."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2

from anvesha.config import Settings
from anvesha.crypto import SecretBox, secret_box_from_settings
from anvesha.db.session import Database
from anvesha.llm.adapters import AnthropicAdapter, OpenAICompatibleAdapter
from anvesha.llm.gateway import LLMGateway
from anvesha.sources.service import SourceService
from anvesha.storage import FileStorage, LocalFileStorage
from anvesha.tools import ToolRegistry, build_registry
from anvesha.tracing import setup_tracing, shutdown_tracing
from anvesha.web.fetch import SafeFetcher


@dataclass
class Services:
    settings: Settings
    db: Database
    vault: SecretBox | None
    # `http` talks to fixed, trusted APIs (model providers, search and scholarly APIs).
    # Arbitrary URLs from users and agents go through `fetcher`, which blocks SSRF.
    http: httpx2.AsyncClient
    fetcher: SafeFetcher
    storage: FileStorage
    gateway: LLMGateway
    sources: SourceService
    tools: ToolRegistry


@asynccontextmanager
async def open_services(
    settings: Settings, *, service: str = "anvesha-api"
) -> AsyncIterator[Services]:
    """``service`` names this process in traces (anvesha-api, anvesha-worker...)."""
    setup_tracing(settings, service=service)
    db = Database(settings.database_url)
    vault = secret_box_from_settings(settings)
    fetcher = SafeFetcher(settings)
    try:
        async with httpx2.AsyncClient(follow_redirects=False) as http:
            openai_adapter = OpenAICompatibleAdapter(http, settings.llm_max_retries)
            gateway = LLMGateway(
                db,
                settings,
                vault,
                {
                    "openai": openai_adapter,
                    "anthropic": AnthropicAdapter(http, settings.llm_max_retries),
                },
                embedder=openai_adapter,
            )
            storage = LocalFileStorage(settings.storage_dir)
            yield Services(
                settings=settings,
                db=db,
                vault=vault,
                http=http,
                fetcher=fetcher,
                storage=storage,
                gateway=gateway,
                sources=SourceService(
                    db=db, settings=settings, fetcher=fetcher, storage=storage, gateway=gateway
                ),
                tools=build_registry(),
            )
    finally:
        await fetcher.aclose()
        await db.dispose()
        shutdown_tracing()
