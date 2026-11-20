"""Network safety checks for URLs that users control (SSRF protection)."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeURLError(ValueError):
    pass


def require_http_url(url: str) -> str:
    """Return the URL's host, or raise if it isn't an absolute http(s) URL."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURLError("URL must be an absolute http:// or https:// URL.")
    return parts.hostname


async def ensure_public_url(url: str) -> None:
    """Raise UnsafeURLError unless every address the URL's host resolves to is public.

    Blocks loopback, private, link-local (including cloud metadata at 169.254.169.254),
    and other non-global ranges.
    """
    host = require_http_url(url)
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Could not resolve host {host!r}.") from exc
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    for address in addresses:
        if not address.is_global:
            raise UnsafeURLError(f"Host {host!r} points to a non-public address.")
