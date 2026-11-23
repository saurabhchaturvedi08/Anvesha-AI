"""Bring the database fully up to date, then exit. Run ``python -m anvesha.migrate``.

Three things share the database, and each manages its own tables:

1. The app's tables, through Alembic migrations (``alembic/versions``).
2. The Procrastinate job queue. Fresh databases get its full schema; existing ones get
   the migration scripts shipped with each newer Procrastinate release. The applied
   version is remembered in ``schema_components``.
3. LangGraph's checkpoint tables, through the checkpointer's own idempotent ``setup()``.

A Postgres advisory lock makes concurrent runs (several containers starting at once)
wait for each other instead of racing.
"""

import asyncio
import logging
import re
import sys
from collections.abc import Callable, Coroutine
from importlib import metadata
from pathlib import Path
from typing import Any

import psycopg
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from procrastinate.schema import SchemaManager

from anvesha.config import Settings, get_settings

logger = logging.getLogger("anvesha.migrate")

# An arbitrary constant: the advisory lock key every migrating process agrees on.
_MIGRATION_LOCK_KEY = 7_201_640_311
_MIGRATION_FILE = re.compile(r"^(\d+)\.(\d+)\.(\d+)_(\d+)_.+\.sql$")
_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise ValueError(f"Unrecognised version {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def alembic_config(database_url: str, ini_path: Path | None = None) -> Config:
    config = Config(str(ini_path or _BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    # Keep the caller's logging setup (alembic.ini would otherwise replace it).
    config.attributes["configure_logger"] = False
    return config


def procrastinate_migrations_after(applied: str, installed: str) -> list[Path]:
    """Procrastinate migration scripts newer than ``applied``, up to ``installed``."""
    low, high = _version_tuple(applied), _version_tuple(installed)
    selected: list[tuple[tuple[int, int, int, int], Path]] = []
    for path in Path(SchemaManager.get_migrations_path()).glob("*.sql"):
        match = _MIGRATION_FILE.match(path.name)
        if match is None:
            continue
        major, minor, patch, step = (int(part) for part in match.groups())
        if low < (major, minor, patch) <= high:
            selected.append(((major, minor, patch, step), path))
    return [path for _, path in sorted(selected)]


async def _migrate_job_queue(connection: psycopg.AsyncConnection[object]) -> None:
    installed = metadata.version("procrastinate")
    exists = await (
        await connection.execute("SELECT to_regclass('public.procrastinate_jobs') IS NOT NULL")
    ).fetchone()
    row = await (
        await connection.execute(
            "SELECT version FROM schema_components WHERE name = 'procrastinate'"
        )
    ).fetchone()
    applied = str(row[0]) if row else None

    if not (exists and exists[0]):
        logger.info("Creating the job queue schema (Procrastinate %s)", installed)
        await connection.execute(SchemaManager.get_schema())  # type: ignore[arg-type]
    elif applied is None:
        # Created outside this tool (e.g. `procrastinate schema --apply`): trust it matches.
        logger.warning("Job queue schema has no recorded version; assuming %s", installed)
    elif _version_tuple(applied) < _version_tuple(installed):
        scripts = procrastinate_migrations_after(applied, installed)
        logger.info("Upgrading the job queue schema %s -> %s", applied, installed)
        for script in scripts:
            logger.info("  applying %s", script.name)
            await connection.execute(script.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    elif _version_tuple(applied) > _version_tuple(installed):
        raise RuntimeError(
            f"The job queue schema ({applied}) is newer than the installed Procrastinate "
            f"({installed}). Deploy the newer version of Anvesha instead of downgrading."
        )
    await connection.execute(
        "INSERT INTO schema_components (name, version) VALUES ('procrastinate', %s) "
        "ON CONFLICT (name) DO UPDATE SET version = EXCLUDED.version, updated_at = now()",
        (installed,),
    )


async def _migrate_extras(settings: Settings) -> None:
    conninfo = settings.psycopg_database_url
    async with await psycopg.AsyncConnection.connect(conninfo) as connection:
        async with connection.transaction():
            await _migrate_job_queue(connection)  # type: ignore[arg-type]
    async with AsyncPostgresSaver.from_conn_string(conninfo) as checkpointer:
        await checkpointer.setup()


def run_async[T](main: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """asyncio.run, with the selector event loop psycopg needs on Windows."""
    if sys.platform == "win32":
        return asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(main())


def migrate(settings: Settings | None = None, *, ini_path: Path | None = None) -> None:
    settings = settings or get_settings()
    with psycopg.connect(settings.psycopg_database_url, autocommit=True) as lock_conn:
        lock_conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_KEY,))
        try:
            # Alembic runs its own event loop (see alembic/env.py), so it goes first.
            command.upgrade(alembic_config(settings.database_url, ini_path), "head")
            run_async(lambda: _migrate_extras(settings))
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_KEY,))
    logger.info("Database is up to date")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    migrate()


if __name__ == "__main__":
    main()
