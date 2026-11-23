"""LangGraph checkpoints in Postgres (tables created by ``python -m anvesha.migrate``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from anvesha.config import Settings


def strict_serializer() -> JsonPlusSerializer:
    """Only plain data types may be restored from checkpoints, never arbitrary classes.

    Graph state is JSON-native by design, so nothing is lost, and someone able to write
    to the checkpoint tables can't get code executed by the worker.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=None)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    pool: AsyncConnectionPool = AsyncConnectionPool(
        settings.psycopg_database_url,
        min_size=1,
        max_size=max(4, settings.worker_concurrency * 2),
        # What AsyncPostgresSaver requires of its connections.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open(wait=True, timeout=30)
    try:
        yield AsyncPostgresSaver(pool, serde=strict_serializer())  # type: ignore[arg-type]
    finally:
        await pool.close()
