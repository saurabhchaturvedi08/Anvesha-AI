"""Test fixtures.

API and database tests run against a real PostgreSQL database (``anvesha_test`` by
default, created automatically). Start one with ``docker compose up -d postgres`` from
the repository root. If it isn't reachable, those tests are skipped locally; CI sets
ANVESHA_REQUIRE_DB=1 so a missing database fails the run instead.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from anvesha.api.app import create_app
from anvesha.config import Settings
from anvesha.crypto import generate_key
from anvesha.db.base import Base
from anvesha.llm.gateway import LLMGateway
from anvesha.llm.types import Completion, Message, ModelRoute, TokenUsage, ToolSpec

BACKEND_DIR = Path(__file__).resolve().parents[1]
# Defaults to the configured database server, using a separate "anvesha_test" database.
TEST_DATABASE_URL = os.environ.get("ANVESHA_TEST_DATABASE_URL") or make_url(
    Settings().database_url
).set(database="anvesha_test").render_as_string(hide_password=False)


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


async def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    engine = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await engine.dispose()


async def _truncate_all_tables(url: str) -> None:
    engine = create_async_engine(url)
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        asyncio.run(_create_database_if_missing(TEST_DATABASE_URL))
    except (OSError, SQLAlchemyError) as exc:
        message = f"PostgreSQL is not reachable for tests ({exc.__class__.__name__}: {exc})"
        if os.environ.get("ANVESHA_REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(f"{message}. Start it with `docker compose up -d postgres`.")
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    return TEST_DATABASE_URL


TEST_SECRET_KEY = generate_key()


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the developer's .env, so local API keys never leak into tests."""
    values: dict[str, Any] = {
        "env": "test",
        "database_url": TEST_DATABASE_URL,
        "secret_key": TEST_SECRET_KEY,
        "secret_key_file": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    """Override in a test module to change settings for the app fixture."""
    return make_settings(database_url=database_url)


@pytest.fixture
async def app(database_url: str, test_settings: Settings) -> AsyncIterator[FastAPI]:
    await _truncate_all_tables(database_url)
    application = create_app(test_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


class FakeProviderAPI:
    """Stands in for provider HTTP APIs. Map URL prefixes to (status, JSON body)."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, Any]] = {}
        self.requests: list[httpx2.Request] = []

    def add(self, url_prefix: str, status_code: int, body: Any) -> None:
        self.routes[url_prefix] = (status_code, body)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        for prefix in sorted(self.routes, key=len, reverse=True):
            if str(request.url).startswith(prefix):
                status_code, body = self.routes[prefix]
                return httpx2.Response(status_code, json=body)
        raise httpx2.ConnectError(f"no fake route for {request.url}", request=request)


@pytest.fixture
async def provider_api(app: FastAPI) -> AsyncIterator[FakeProviderAPI]:
    """Route the app's outbound HTTP (key checks, model lists) to a fake."""
    fake = FakeProviderAPI()
    original = app.state.http
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler)) as http:
        app.state.http = http
        yield fake
    app.state.http = original


class FakeAdapter:
    """A chat adapter that returns a canned completion (or raises) and records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.usage = TokenUsage(input_tokens=1000, output_tokens=500)

    async def complete(
        self,
        route: ModelRoute,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
        request_timeout: float,
    ) -> Completion:
        self.calls.append({"route": route, "messages": list(messages), "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        return Completion(
            text="fake reply",
            tool_calls=(),
            usage=self.usage,
            provider=route.access.provider,
            model=route.model,
            finish_reason="stop",
        )


@pytest.fixture
def fake_llm(app: FastAPI) -> FakeAdapter:
    """Replace real model calls with FakeAdapter for every provider."""
    adapter = FakeAdapter()
    app.state.gateway = LLMGateway(
        app.state.db,
        app.state.settings,
        app.state.vault,
        {"openai": adapter, "anthropic": adapter},
    )
    return adapter
