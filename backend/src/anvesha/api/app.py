from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import ProgrammingError
from starlette.routing import Route

from anvesha import __version__
from anvesha.api.deps import get_principal
from anvesha.api.routers import (
    access_tokens,
    chat,
    connections,
    health,
    meta,
    model_preferences,
    oauth_consent,
    projects,
    providers,
    runs,
    search,
    sources,
    tools,
    usage,
    workspaces,
)
from anvesha.auth import JwtVerifier
from anvesha.chat import ChatService
from anvesha.config import Settings, get_settings
from anvesha.errors import AppError, RateLimitedError
from anvesha.events.broker import RunEventBroker
from anvesha.mcp import MCP_PATH, AnveshaMCP, McpServices
from anvesha.mcp.oauth import AnveshaOAuthProvider, OAuthDeps, oauth_routes, resource_metadata_url
from anvesha.services import open_services
from anvesha.tenancy import ensure_local_principal
from anvesha.worker.queue import JobQueue


def create_public_app(settings: Settings | None = None) -> FastAPI:
    """Only what MCP clients on the internet need: ``/mcp``, OAuth sign-in, and health.

    Put this one (not ``create_app``) behind a tunnel or reverse proxy at
    ``ANVESHA_MCP_PUBLIC_URL``. The REST API stays private: in single-user mode it has no
    sign-in. Run with ``uvicorn anvesha.api.app:create_public_app --factory``.
    """
    return create_app(settings, public=True)


def _check_mcp_settings(settings: Settings, *, public: bool) -> None:
    if public and not settings.mcp_enabled:
        raise RuntimeError("The public app serves MCP only; set ANVESHA_MCP_ENABLED=true.")
    # In multi-user mode people approve connections in the web app, signed in; the
    # approval secret is only for single-user mode, which has no accounts.
    if (
        settings.mcp_oauth_enabled
        and settings.auth_mode == "local"
        and settings.mcp_approval_secret is None
    ):
        raise RuntimeError(
            "ANVESHA_MCP_PUBLIC_URL is set, so OAuth sign-in is on, but "
            "ANVESHA_MCP_APPROVAL_SECRET is not. Set it to a long random value: in "
            "single-user mode it is what stops strangers approving their own connections."
        )


def create_app(settings: Settings | None = None, *, public: bool = False) -> FastAPI:
    """Build the API. Run with ``uvicorn anvesha.api.app:create_app --factory``."""
    settings = settings or get_settings()
    _check_mcp_settings(settings, public=public)

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
            # Multi-user mode: requests carry the web app's JWTs, checked against its keys.
            app.state.jwt_verifier = (
                JwtVerifier(settings, services.http) if settings.auth_mode == "jwt" else None
            )
            app.state.oauth_provider = oauth
            if settings.mcp_oauth_enabled and services.vault is None:
                raise RuntimeError(
                    "OAuth sign-in stores client secrets encrypted, so it needs a master key "
                    "(ANVESHA_SECRET_KEY or ANVESHA_SECRET_KEY_FILE)."
                )
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
            # Chat replies are written in this process, in the background.
            chat_replies = ChatService(settings)
            app.state.queue = queue
            app.state.broker = broker
            app.state.chat = chat_replies
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(queue.app.open_async())
                if mcp_server is not None:
                    await stack.enter_async_context(mcp_server.run())
                await broker.start()
                try:
                    yield
                finally:
                    await chat_replies.aclose()
                    await broker.stop()

    # The public app has no REST API, so nothing to document.
    app = FastAPI(
        title="Anvesha API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if public else "/docs",
        redoc_url=None if public else "/redoc",
        openapi_url=None if public else "/openapi.json",
    )

    def mcp_services() -> McpServices:
        state = app.state
        return McpServices(
            settings=state.settings,
            db=state.db,
            vault=state.vault,
            http=state.http,
            gateway=state.gateway,
            sources=state.sources,
            tools=state.tools,
            queue=state.queue,
        )

    def oauth_deps() -> OAuthDeps:
        state = app.state
        return OAuthDeps(
            settings=state.settings,
            db=state.db,
            vault=state.vault,
            principal=state.local_principal,
        )

    oauth = AnveshaOAuthProvider(oauth_deps) if settings.mcp_oauth_enabled else None
    mcp_server = (
        AnveshaMCP(
            mcp_services,
            resource_metadata_url=resource_metadata_url(settings) if oauth else None,
        )
        if settings.mcp_enabled
        else None
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        headers = (
            {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": str(exc), "code": exc.code},
            headers=headers,
        )

    app.include_router(health.router)
    if not public:
        for router in (
            meta.router,
            projects.router,
            providers.router,
            model_preferences.router,
            usage.router,
            search.router,
            sources.router,
            tools.router,
            runs.router,
            chat.router,
            access_tokens.router,
            connections.router,
            workspaces.router,
            oauth_consent.router,
        ):
            # Every REST route needs a signed-in user in multi-user mode, including the
            # few that don't otherwise ask who is calling (cached per request).
            app.include_router(router, dependencies=[Depends(get_principal)])
    if oauth is not None:
        app.router.routes.extend(oauth_routes(oauth, settings))
    if mcp_server is not None:
        # Any HTTP method: the MCP transport answers POST, GET and DELETE itself.
        app.router.routes.append(Route(MCP_PATH, endpoint=mcp_server.asgi_app()))
    return app
