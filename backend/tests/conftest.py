"""Test fixtures.

API and database tests run against a real PostgreSQL database (``anvesha_test`` by
default, created automatically). Start one with ``docker compose up -d postgres`` from
the repository root. If it isn't reachable, those tests are skipped locally; CI sets
ANVESHA_REQUIRE_DB=1 so a missing database fails the run instead.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

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
from anvesha.db.base import Base

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


@pytest.fixture
async def app(database_url: str) -> AsyncIterator[FastAPI]:
    await _truncate_all_tables(database_url)
    application = create_app(Settings(env="test", database_url=database_url))
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
