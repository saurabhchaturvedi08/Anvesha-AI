"""Run activity events: stored in ``run_events`` and announced with Postgres NOTIFY.

Chat replies use the same channel to announce progress (keyed by the reply's id).

The table is the source of truth (clients replay from it after reconnecting); the
notification only wakes up streams that are waiting, so a missed one costs latency,
never data.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.db.models import RunEvent
from anvesha.db.session import Database

CHANNEL = "anvesha_run_events"
# Keep payloads small: events are for following progress, the API has the full records.
MAX_TEXT = 500

logger = logging.getLogger(__name__)


def clip(text: str | None, limit: int = MAX_TEXT) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def add_event(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    type: str,
    data: dict[str, Any] | None = None,
    agent: str | None = None,
) -> RunEvent:
    """Add an event to the caller's transaction. Listeners are notified on commit."""
    event = RunEvent(
        run_id=run_id, workspace_id=workspace_id, type=type, agent=agent, data=data or {}
    )
    session.add(event)
    await session.flush()
    await notify(session, run_id)
    return event


async def notify(session: AsyncSession, key: uuid.UUID) -> None:
    """Wake streams following ``key`` (a run, or a chat reply) when the transaction commits."""
    await session.execute(select(func.pg_notify(CHANNEL, str(key))))


async def emit(
    db: Database,
    *,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    type: str,
    data: dict[str, Any] | None = None,
    agent: str | None = None,
) -> None:
    """Record an event in its own transaction. Never raises: progress reporting must not
    break the research itself."""
    try:
        async with db.session() as session:
            await add_event(
                session, run_id=run_id, workspace_id=workspace_id, type=type, data=data, agent=agent
            )
            await session.commit()
    except SQLAlchemyError:
        logger.exception("Failed to record %s event for run %s", type, run_id)
