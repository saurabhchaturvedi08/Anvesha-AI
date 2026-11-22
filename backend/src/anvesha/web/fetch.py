"""Safe fetching of web pages and documents chosen by users or agents.

Guards: http(s) only, no private/internal addresses (checked per connection, see
transport.py), bounded redirects, overall timeout, size cap while streaming, a content
type allowlist, optional robots.txt, and a cap on concurrent fetches.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx2

from anvesha.config import Settings
from anvesha.errors import AppError
from anvesha.net import UnsafeURLError, require_http_url
from anvesha.web.robots import RobotsPolicy
from anvesha.web.transport import guarded_transport

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
PDF_TYPE = "application/pdf"
SUPPORTED_TYPES = HTML_TYPES | TEXT_TYPES | {PDF_TYPE}
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class FetchError(AppError):
    code = "fetch_failed"
    http_status = 502
    retryable = True


@dataclass(frozen=True)
class FetchedResource:
    url: str  # final URL, after redirects
    requested_url: str
    status_code: int
    content_type: str  # media type without parameters, e.g. "text/html"
    charset: str | None
    body: bytes = field(repr=False)
    fetched_at: datetime


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication: lower-case scheme and host, no fragment."""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    if (parts.scheme == "http" and netloc.endswith(":80")) or (
        parts.scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def _parse_content_type(header: str | None) -> tuple[str, str | None]:
    if not header:
        return "", None
    media_type, _, params = header.partition(";")
    charset = None
    for param in params.split(";"):
        name, _, value = param.strip().partition("=")
        if name.lower() == "charset" and value:
            charset = value.strip().strip("\"'")
    return media_type.strip().lower(), charset


def _sniff_type(media_type: str, body: bytes) -> str:
    """Trust magic bytes over a missing or generic Content-Type."""
    head = body[:1024].lstrip()
    if head.startswith(b"%PDF-"):
        return PDF_TYPE
    if media_type in ("", "application/octet-stream", "binary/octet-stream"):
        lowered = head[:256].lower()
        if lowered.startswith((b"<!doctype html", b"<html")):
            return "text/html"
    return media_type


class SafeFetcher:
    def __init__(
        self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._client = httpx2.AsyncClient(
            transport=transport
            or guarded_transport(allow_private=settings.fetch_allow_private_networks),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx2.Timeout(settings.fetch_timeout_seconds, connect=10.0),
            headers={
                "User-Agent": settings.http_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,"
                "text/plain;q=0.8,*/*;q=0.1",
                "Accept-Language": "en;q=1.0, *;q=0.5",
            },
        )
        self._robots = RobotsPolicy(self._client, settings.robots_user_agent)
        self._semaphore = asyncio.Semaphore(settings.fetch_max_concurrency)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        respect_robots: bool = True,
        accept: frozenset[str] = SUPPORTED_TYPES,
        max_bytes: int | None = None,
    ) -> FetchedResource:
        """Download one resource. Raises FetchError with a code describing what went wrong."""
        limit = max_bytes or self._settings.fetch_max_bytes
        async with self._semaphore:
            try:
                async with asyncio.timeout(self._settings.fetch_timeout_seconds):
                    return await self._fetch(url, respect_robots, accept, limit)
            except TimeoutError as exc:
                raise FetchError(
                    f"Timed out fetching {url}.", code="fetch_timeout", http_status=504
                ) from exc
            except UnsafeURLError as exc:
                raise FetchError(
                    str(exc), code="fetch_blocked", http_status=422, retryable=False
                ) from exc
            except httpx2.TimeoutException as exc:
                raise FetchError(
                    f"Timed out fetching {url}.", code="fetch_timeout", http_status=504
                ) from exc
            except httpx2.HTTPError as exc:
                raise FetchError(f"Could not fetch {url}: {exc.__class__.__name__}.") from exc

    async def _fetch(
        self, url: str, respect_robots: bool, accept: frozenset[str], limit: int
    ) -> FetchedResource:
        requested = url
        for _ in range(self._settings.fetch_max_redirects + 1):
            require_http_url(url)
            if respect_robots and not await self._robots.allowed(url):
                raise FetchError(
                    f"{url} disallows automated access (robots.txt).",
                    code="robots_disallowed",
                    http_status=422,
                    retryable=False,
                )
            async with self._client.stream("GET", url) as response:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError(f"{url} redirected without a Location header.")
                    url = urljoin(url, location)
                    continue
                if response.status_code >= 400:
                    raise FetchError(
                        f"{url} returned HTTP {response.status_code}.",
                        code="http_error",
                        http_status=502 if response.status_code >= 500 else 422,
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                media_type, charset = _parse_content_type(response.headers.get("content-type"))
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise self._too_large(url, limit)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise self._too_large(url, limit)
                media_type = _sniff_type(media_type, bytes(body))
                if media_type not in accept:
                    raise FetchError(
                        f"{url} is {media_type or 'an unknown type'}, which isn't supported. "
                        "Supported: web pages, plain text, Markdown, and PDF.",
                        code="unsupported_content_type",
                        http_status=422,
                        retryable=False,
                    )
                return FetchedResource(
                    url=str(response.url),
                    requested_url=requested,
                    status_code=response.status_code,
                    content_type=media_type,
                    charset=charset,
                    body=bytes(body),
                    fetched_at=datetime.now(UTC),
                )
        raise FetchError(
            f"Too many redirects fetching {requested}.", code="too_many_redirects", retryable=False
        )

    @staticmethod
    def _too_large(url: str, limit: int) -> FetchError:
        return FetchError(
            f"{url} is larger than the {limit // (1024 * 1024)} MB limit.",
            code="fetch_too_large",
            http_status=422,
            retryable=False,
        )
