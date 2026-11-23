from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
    runs,
    search,
    sources,
    tools,
    usage,
)
from anvesha.config import Settings, get_settings
from anvesha.errors import AppError
from anvesha.events.broker import RunEventBroker
from anvesha.services import open_services
from anvesha.tenancy import ensure_local_principal
from anvesha.worker.queue import JobQueue


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Run with ``uvicorn anvesha.api.app:create_app --factory``."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_services(settings) as services:
            app.state.settings = settings
            app.state.db = services.db
            app.state.vault = services.vault
            app.state.http = services.http
            app.state.gateway = services.gateway
            app.state.fetcher = services.fetcher
            app.state.storage = services.storage
            app.state.sources = services.sources
            app.state.tools = services.tools
            async with services.db.session() as session:
                try:
                    app.state.local_principal = await ensure_local_principal(
                        session, settings.local_user_email
                    )
                except ProgrammingError as exc:
                    raise RuntimeError(
                        "The database schema is missing or outdated. "
                        "Run `python -m anvesha.migrate` in backend/ first."
                    ) from exc
            # Runs execute in the worker; the API only queues them and streams progress.
            queue = JobQueue(settings)
            broker = RunEventBroker(settings.postgres_dsn)
            app.state.queue = queue
            app.state.broker = broker
            async with queue.app.open_async():
                await broker.start()
                try:
                    yield
                finally:
                    await broker.stop()

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
    app.include_router(runs.router)
    return app
