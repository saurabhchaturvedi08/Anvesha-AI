"""Server-Sent Events for a run: replay stored events, then follow new ones live."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy import select

from anvesha.db.models import Run, RunEvent
from anvesha.db.session import Database
from anvesha.events.broker import RunEventBroker

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
# A run waiting for the user doesn't change by itself, but the stream stays open so the
# client sees the run continue once the plan is approved.
PAGE_SIZE = 200
KEEPALIVE_SECONDS = 15.0
POLL_SECONDS = 2.0  # used while the LISTEN connection is down


def event_payload(event: RunEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "agent": event.agent,
        "data": event.data,
        "created_at": event.created_at.isoformat(),
    }


def format_sse(event_id: int | None, event_type: str, data: dict[str, Any]) -> bytes:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    # json.dumps never emits raw newlines, so the payload is always one data line.
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return ("\n".join(lines) + "\n\n").encode()


async def stream_run_events(
    db: Database,
    broker: RunEventBroker,
    run_id: uuid.UUID,
    *,
    after_id: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> AsyncIterator[bytes]:
    """Yield SSE frames until the run ends (then an ``end`` event) or the client leaves.

    The run's status is read *before* each batch of events. The worker commits a run's
    final status together with its final event, so once a terminal status is seen, the
    following read is guaranteed to include everything.
    """
    yield b"retry: 3000\n\n"
    last_id = after_id
    with broker.subscribe(run_id) as wake:
        while True:
            wake.clear()
            async with db.session() as session:
                status = await session.scalar(select(Run.status).where(Run.id == run_id))
                events = (
                    await session.scalars(
                        select(RunEvent)
                        .where(RunEvent.run_id == run_id, RunEvent.id > last_id)
                        .order_by(RunEvent.id)
                        .limit(PAGE_SIZE)
                    )
                ).all()
            for event in events:
                last_id = event.id
                yield format_sse(event.id, event.type, event_payload(event))
            if len(events) == PAGE_SIZE:
                continue  # more to replay right away
            if status is None or status in TERMINAL_STATUSES:
                yield format_sse(None, "end", {"status": status or "deleted"})
                return
            if await is_disconnected():
                return
            timeout = keepalive_seconds if broker.healthy else min(POLL_SECONDS, keepalive_seconds)
            try:
                await asyncio.wait_for(wake.wait(), timeout=timeout)
            except TimeoutError:
                yield b": keepalive\n\n"
