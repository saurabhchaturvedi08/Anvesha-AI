"""robots.txt checks for pages that agents fetch on their own initiative."""

import time
from collections import OrderedDict
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx2

from anvesha.net import UnsafeURLError

ROBOTS_MAX_BYTES = 512 * 1024
CACHE_TTL_SECONDS = 3600.0
CACHE_MAX_ORIGINS = 1024


class RobotsPolicy:
    """Caches each origin's robots.txt for an hour.

    A missing robots.txt (4xx) allows everything. If it can't be fetched (network error
    or 5xx) we also allow: a user asked for this research, and a flaky robots.txt
    shouldn't silently drop sources. Explicit Disallow rules are always honoured.
    """

    def __init__(self, client: httpx2.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._cache: OrderedDict[str, tuple[float, RobotFileParser | None]] = OrderedDict()

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = await self._parser(origin)
        return parser is None or parser.can_fetch(self._user_agent, url)

    async def _parser(self, origin: str) -> RobotFileParser | None:
        cached = self._cache.get(origin)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            self._cache.move_to_end(origin)
            return cached[1]
        parser = await self._load(origin)
        self._cache[origin] = (now + CACHE_TTL_SECONDS, parser)
        self._cache.move_to_end(origin)
        while len(self._cache) > CACHE_MAX_ORIGINS:
            self._cache.popitem(last=False)
        return parser

    async def _load(self, origin: str) -> RobotFileParser | None:
        try:
            async with self._client.stream(
                "GET", f"{origin}/robots.txt", follow_redirects=True
            ) as response:
                if response.status_code != 200:
                    return None
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > ROBOTS_MAX_BYTES:
                        break
        except (httpx2.HTTPError, UnsafeURLError):
            return None
        parser = RobotFileParser()
        parser.parse(body[:ROBOTS_MAX_BYTES].decode("utf-8", errors="replace").splitlines())
        return parser
