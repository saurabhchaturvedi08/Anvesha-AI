"""Rate limits on expensive operations, counted in Postgres so all API replicas agree.

Fixed windows: each request adds one to the counter for its key and the current window,
and is refused once the count passes the limit. Simple, one statement per request, and
precise enough for abuse protection (a burst at a window edge can reach twice the limit).
"""

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from anvesha.config import Settings
from anvesha.db.models import RateLimitCounter
from anvesha.db.session import Database
from anvesha.errors import RateLimitedError
from anvesha.tenancy import Principal

Bucket = Literal["runs", "chat", "clarify", "tools"]


@dataclass(frozen=True)
class Limit:
    setting: str  # the Settings field holding the limit
    window_seconds: int
    scope: Literal["workspace", "user"]
    what: str  # for the error message


LIMITS: dict[Bucket, Limit] = {
    "runs": Limit("rate_limit_runs_per_hour", 3600, "workspace", "research runs"),
    "chat": Limit("rate_limit_chat_per_hour", 3600, "user", "chat messages"),
    "clarify": Limit("rate_limit_clarify_per_hour", 3600, "user", "question checks"),
    "tools": Limit("rate_limit_tools_per_10_minutes", 600, "user", "searches and tool calls"),
}


async def hit(db: Database, key: str, window_seconds: int, *, now: float | None = None) -> int:
    """Count one request for ``key`` in the current window; returns the window's count."""
    now = time.time() if now is None else now
    window_start = datetime.fromtimestamp(now - now % window_seconds, UTC)
    statement = insert(RateLimitCounter).values(key=key, window_start=window_start, count=1)
    async with db.session() as session:
        count = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=[RateLimitCounter.key, RateLimitCounter.window_start],
                set_={"count": RateLimitCounter.count + 1},
            ).returning(RateLimitCounter.count)
        )
        await session.commit()
    return int(count or 0)


async def check_rate_limit(
    db: Database, settings: Settings, bucket: Bucket, principal: Principal
) -> None:
    """Raise RateLimitedError if this request goes over the bucket's limit."""
    if not settings.rate_limits_on:
        return
    limit = LIMITS[bucket]
    maximum = int(getattr(settings, limit.setting))
    if maximum <= 0:
        return
    who = principal.workspace_id if limit.scope == "workspace" else principal.user_id
    now = time.time()
    count = await hit(db, f"{bucket}:{who}", limit.window_seconds, now=now)
    if count > maximum:
        retry_after = max(1, math.ceil(limit.window_seconds - now % limit.window_seconds))
        scope = "this workspace" if limit.scope == "workspace" else "you"
        raise RateLimitedError(
            f"Too many {limit.what}: {scope} can make {maximum} every "
            f"{_duration(limit.window_seconds)}. Try again in {_duration(retry_after)}.",
            retry_after=retry_after,
        )


def _duration(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return "hour" if hours == 1 else f"{hours} hours"
    if seconds >= 60:
        minutes = math.ceil(seconds / 60)
        return "minute" if minutes == 1 else f"{minutes} minutes"
    return "a few seconds" if seconds < 10 else f"{seconds} seconds"


async def prune_rate_limits(db: Database, older_than: timedelta = timedelta(days=1)) -> None:
    """Drop old windows (run hourly by the worker)."""
    async with db.session() as session:
        await session.execute(
            delete(RateLimitCounter).where(
                RateLimitCounter.window_start < datetime.now(UTC) - older_than
            )
        )
        await session.commit()
