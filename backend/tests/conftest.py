"""Test fixtures.

API and database tests run against a real PostgreSQL database (``anvesha_test`` by
default, created automatically). Start one with ``docker compose up -d postgres`` from
the repository root. If it isn't reachable, those tests are skipped locally; CI sets
ANVESHA_REQUIRE_DB=1 so a missing database fails the run instead.
"""

import asyncio
import hashlib
import math
import os
import re
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest
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
from anvesha.llm.types import Completion, Embeddings, Message, ModelRoute, TokenUsage, ToolSpec
from anvesha.migrate import migrate
from anvesha.sources.service import SourceService
from anvesha.web.fetch import SafeFetcher

BACKEND_DIR = Path(__file__).resolve().parents[1]
# Defaults to the configured database server, using a separate "anvesha_test" database.
TEST_DATABASE_URL = os.environ.get("ANVESHA_TEST_DATABASE_URL") or make_url(
    Settings().database_url
).set(database="anvesha_test").render_as_string(hide_password=False)
# libpq tries IPv6 first for "localhost"; where Docker only listens on IPv4 (common on
# Windows) every psycopg connection would wait for a timeout first.
TEST_DATABASE_URL = TEST_DATABASE_URL.replace("@localhost:", "@127.0.0.1:")


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]] | None:
    """psycopg (job queue, checkpoints) can't use Windows' default Proactor event loop."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


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


# Tables managed by the job queue and LangGraph, emptied between tests as well.
EXTERNAL_TABLES = ("procrastinate_jobs", "checkpoints", "checkpoint_writes", "checkpoint_blobs")


async def _truncate_all_tables(url: str) -> None:
    engine = create_async_engine(url)
    names = [
        table.name for table in Base.metadata.sorted_tables if table.name != "schema_components"
    ]
    tables = ", ".join(f'"{name}"' for name in (*names, *EXTERNAL_TABLES))
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        asyncio.run(
            _create_database_if_missing(TEST_DATABASE_URL),
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
        )
    except (OSError, SQLAlchemyError) as exc:
        message = f"PostgreSQL is not reachable for tests ({exc.__class__.__name__}: {exc})"
        if os.environ.get("ANVESHA_REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(f"{message}. Start it with `docker compose up -d postgres`.")
    # The full setup the deployment runs: Alembic, the job queue, LangGraph checkpoints.
    migrate(make_settings(database_url=TEST_DATABASE_URL))
    return TEST_DATABASE_URL


TEST_SECRET_KEY = generate_key()
# Stored files from tests go to a throwaway directory, never into the source tree.
TEST_STORAGE_DIR = Path(tempfile.mkdtemp(prefix="anvesha-test-files-"))


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the developer's .env, so local API keys never leak into tests."""
    values: dict[str, Any] = {
        "env": "test",
        "database_url": TEST_DATABASE_URL,
        "secret_key": TEST_SECRET_KEY,
        "secret_key_file": None,
        "storage_dir": TEST_STORAGE_DIR,
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
    """Stands in for external HTTP APIs. Map URL prefixes to (status, body).

    A ``bytes`` body is sent as-is with ``content_type``; anything else is sent as JSON.
    """

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, Any, str]] = {}
        self.requests: list[httpx2.Request] = []

    def add(
        self, url_prefix: str, status_code: int, body: Any, content_type: str = "application/xml"
    ) -> None:
        self.routes[url_prefix] = (status_code, body, content_type)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        for prefix in sorted(self.routes, key=len, reverse=True):
            if str(request.url).startswith(prefix):
                status_code, body, content_type = self.routes[prefix]
                if isinstance(body, bytes):
                    return httpx2.Response(
                        status_code, content=body, headers={"content-type": content_type}
                    )
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


def fake_embedding(text: str, dimensions: int = 32) -> list[float]:
    """Deterministic bag-of-words vector: texts sharing words get similar vectors."""
    vector = [0.0] * dimensions
    for word in re.findall(r"\w+", text.lower()):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dimensions] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeAdapter:
    """A chat and embedding adapter with canned output (or errors) that records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.embed_calls: list[list[str]] = []
        self.error: Exception | None = None
        self.embed_error: Exception | None = None
        self.usage = TokenUsage(input_tokens=1000, output_tokens=500)

    async def embed(
        self, route: ModelRoute, texts: Sequence[str], *, request_timeout: float
    ) -> Embeddings:
        self.embed_calls.append(list(texts))
        if self.embed_error is not None:
            raise self.embed_error
        return Embeddings(
            vectors=[fake_embedding(text) for text in texts],
            usage=TokenUsage(input_tokens=sum(len(t.split()) for t in texts)),
            provider=route.access.provider,
            model=route.model,
        )

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


def _rebuild_source_service(app: FastAPI) -> None:
    app.state.sources = SourceService(
        db=app.state.db,
        settings=app.state.settings,
        fetcher=app.state.fetcher,
        storage=app.state.storage,
        gateway=app.state.gateway,
    )


@pytest.fixture
def fake_llm(app: FastAPI) -> FakeAdapter:
    """Replace real model calls (chat and embeddings) with FakeAdapter for every provider."""
    adapter = FakeAdapter()
    app.state.gateway = LLMGateway(
        app.state.db,
        app.state.settings,
        app.state.vault,
        {"openai": adapter, "anthropic": adapter},
        embedder=adapter,
    )
    _rebuild_source_service(app)
    return adapter


class FakeWeb:
    """Websites for the fetcher. Map exact URLs to (status, headers, body)."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.requests: list[str] = []

    def page(
        self,
        url: str,
        body: str | bytes,
        content_type: str = "text/html; charset=utf-8",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        content = body.encode() if isinstance(body, str) else body
        self.pages[url] = (status_code, {"content-type": content_type, **(headers or {})}, content)

    def redirect(self, url: str, location: str, status_code: int = 301) -> None:
        self.pages[url] = (status_code, {"location": location}, b"")

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.pages:
            status_code, headers, content = self.pages[url]
            return httpx2.Response(status_code, headers=headers, content=content)
        if url.endswith("/robots.txt"):
            return httpx2.Response(404)
        raise httpx2.ConnectError(f"no fake page for {url}", request=request)


@pytest.fixture
async def fake_web(app: FastAPI) -> AsyncIterator[FakeWeb]:
    """Serve pages to the app's fetcher from FakeWeb instead of the internet."""
    web = FakeWeb()
    original = app.state.fetcher
    fetcher = SafeFetcher(app.state.settings, transport=httpx2.MockTransport(web.handler))
    app.state.fetcher = fetcher
    _rebuild_source_service(app)
    yield web
    await fetcher.aclose()
    app.state.fetcher = original
    _rebuild_source_service(app)


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page (for extraction tests)."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, page_text in enumerate(pages):
        escaped = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 11 Tf 40 760 Td ({escaped}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            f"<< /Font << /F1 3 0 R >> >> /Contents {page_ids[index] + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
