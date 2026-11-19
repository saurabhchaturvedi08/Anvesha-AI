from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import ProgrammingError

from anvesha import __version__
from anvesha.api.routers import health, projects
from anvesha.config import Settings, get_settings
from anvesha.db.session import Database
from anvesha.tenancy import ensure_local_principal


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Run with ``uvicorn anvesha.api.app:create_app --factory``."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        app.state.settings = settings
        app.state.db = db
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
            await db.dispose()

    app = FastAPI(title="Anvesha API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(projects.router)
    return app
