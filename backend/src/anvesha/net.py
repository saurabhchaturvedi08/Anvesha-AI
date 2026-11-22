"""Network safety checks for URLs that users or agents control (SSRF protection)."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeURLError(ValueError):
    pass


def require_http_url(url: str) -> str:
    """Return the URL's host, or raise if it isn't an absolute http(s) URL."""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port  # noqa: B018 (raises ValueError for an invalid port)
    except ValueError as exc:
        raise UnsafeURLError("URL is malformed.") from exc
    if parts.scheme not in ("http", "https") or not hostname:
        raise UnsafeURLError("URL must be an absolute http:// or https:// URL.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    return hostname


def is_public_address(address: IPAddress) -> bool:
    """True for globally routable unicast addresses.

    Blocks loopback, private, link-local (including cloud metadata at 169.254.169.254),
    carrier-grade NAT, multicast, reserved, and IPv4 addresses tunnelled through IPv6.
    """
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is not None:
            return is_public_address(embedded)
        if address.teredo is not None:
            return False
    return address.is_global and not address.is_multicast


async def resolve_host(host: str) -> list[IPAddress]:
    """All addresses a host name resolves to (or the host itself if it's an IP literal)."""
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeURLError(f"Could not resolve host {host!r}.") from exc
    addresses: list[IPAddress] = []
    for info in infos:
        # Strip any IPv6 zone id ("fe80::1%eth0") before parsing.
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeURLError(f"Could not resolve host {host!r}.")
    return addresses


async def resolve_allowed(host: str, *, allow_private: bool) -> list[IPAddress]:
    """Resolve a host, raising UnsafeURLError if any of its addresses is off-limits."""
    addresses = await resolve_host(host)
    if not allow_private:
        for address in addresses:
            if not is_public_address(address):
                raise UnsafeURLError(f"Host {host!r} points to a non-public address.")
    return addresses


async def ensure_public_url(url: str) -> None:
    """Raise UnsafeURLError unless every address the URL's host resolves to is public."""
    await resolve_allowed(require_http_url(url), allow_private=False)
