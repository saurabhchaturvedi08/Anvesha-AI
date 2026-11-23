"""Wakes up event streams when a run has new events (Postgres LISTEN on one connection).

One listening connection per API process serves every open stream. If it drops, the
broker reconnects in the background; meanwhile streams fall back to polling.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterator

import asyncpg

from anvesha.events import CHANNEL

logger = logging.getLogger(__name__)


class RunEventBroker:
    def __init__(self, dsn: str, *, reconnect_delay: float = 2.0) -> None:
        self._dsn = dsn
        self._reconnect_delay = reconnect_delay
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()

    @property
    def healthy(self) -> bool:
        return self._connected.is_set()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="run-event-broker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @contextlib.contextmanager
    def subscribe(self, run_id: uuid.UUID) -> Iterator[asyncio.Event]:
        """An event that is set whenever the run gets new events. Clear it before reading."""
        waiter = asyncio.Event()
        key = str(run_id)
        self._waiters[key].add(waiter)
        try:
            yield waiter
        finally:
            self._waiters[key].discard(waiter)
            if not self._waiters[key]:
                del self._waiters[key]

    def _notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        for waiter in self._waiters.get(payload, ()):
            waiter.set()

    def _wake_all(self) -> None:
        # After a reconnect, notifications may have been missed: let everyone re-check.
        for waiters in self._waiters.values():
            for waiter in waiters:
                waiter.set()

    async def _run(self) -> None:
        while True:
            connection: asyncpg.Connection | None = None
            try:
                connection = await asyncpg.connect(self._dsn, timeout=10)
                lost = asyncio.Event()
                connection.add_termination_listener(lambda _conn, lost=lost: lost.set())
                await connection.add_listener(CHANNEL, self._notify)
                self._connected.set()
                self._wake_all()
                while not lost.is_set():
                    try:
                        await asyncio.wait_for(lost.wait(), timeout=30)
                    except TimeoutError:
                        # A silently dropped network connection never fires the
                        # termination listener; a ping notices it.
                        await connection.fetchval("SELECT 1", timeout=5)
                logger.warning("Lost the event listener connection; reconnecting")
            except asyncio.CancelledError:
                raise
            except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                logger.warning("Event listener unavailable (%s); streams will poll", exc)
            finally:
                self._connected.clear()
                if connection is not None and not connection.is_closed():
                    with contextlib.suppress(Exception):
                        await connection.close(timeout=2)
            await asyncio.sleep(self._reconnect_delay)
