from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import ProgrammingError

from anvesha import __version__
from anvesha.api.routers import (
    health,
    model_preferences,
    projects,
    providers,
    search,
    sources,
    tools,
    usage,
)
from anvesha.config import Settings, get_settings
from anvesha.crypto import secret_box_from_settings
from anvesha.db.session import Database
from anvesha.errors import AppError
from anvesha.llm.adapters import AnthropicAdapter, OpenAICompatibleAdapter
from anvesha.llm.gateway import LLMGateway
from anvesha.sources.service import SourceService
from anvesha.storage import LocalFileStorage
from anvesha.tenancy import ensure_local_principal
from anvesha.tools import build_registry
from anvesha.web.fetch import SafeFetcher


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Run with ``uvicorn anvesha.api.app:create_app --factory``."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        vault = secret_box_from_settings(settings)
        # `http` talks to fixed, trusted APIs (model providers, search and scholarly APIs).
        # Arbitrary URLs from users and agents go through `fetcher`, which blocks SSRF.
        fetcher = SafeFetcher(settings)
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
            app.state.settings = settings
            app.state.db = db
            app.state.vault = vault
            app.state.http = http
            app.state.gateway = gateway
            app.state.fetcher = fetcher
            app.state.storage = storage
            app.state.sources = SourceService(
                db=db, settings=settings, fetcher=fetcher, storage=storage, gateway=gateway
            )
            app.state.tools = build_registry()
            try:
                async with db.session() as session:
                    try:
                        app.state.local_principal = await ensure_local_principal(
                            session, settings.local_user_email
                        )
                    except ProgrammingError as exc:
                        raise RuntimeError(
                            "The database schema is missing or outdated. "
                            "Run `uv run alembic upgrade head` in backend/ first."
                        ) from exc
                yield
            finally:
                await fetcher.aclose()
                await db.dispose()

    app = FastAPI(title="Anvesha API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status, content={"detail": str(exc), "code": exc.code}
        )

    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(providers.router)
    app.include_router(model_preferences.router)
    app.include_router(usage.router)
    app.include_router(search.router)
    app.include_router(sources.router)
    app.include_router(tools.router)
    return app
