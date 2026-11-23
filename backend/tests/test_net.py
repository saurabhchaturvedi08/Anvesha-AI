import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from anvesha.net import (
    UnsafeURLError,
    ensure_public_url,
    is_public_address,
    require_http_url,
    resolve_allowed,
)
from anvesha.web.transport import guarded_transport


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:8080",
        "http://10.0.0.5/v1",
        "http://192.168.1.10",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://[::1]/v1",
        "http://[::ffff:127.0.0.1]/",  # IPv4 loopback tunnelled through IPv6
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://0.0.0.0/",
    ],
)
async def test_private_addresses_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_public_url(url)


async def test_public_address_is_allowed() -> None:
    await ensure_public_url("https://8.8.8.8/v1")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "example.com",
        "",
        "http://user:pass@example.com/",
        "http://example.com:99999/",
    ],
)
def test_non_http_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        require_http_url(url)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("224.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("2002:7f00:0001::", False),  # 6to4 wrapping 127.0.0.1
        ("fe80::1", False),
        ("fc00::1", False),
    ],
)
def test_public_address_classification(address: str, public: bool) -> None:
    assert is_public_address(ipaddress.ip_address(address)) is public


@pytest.fixture
async def dns(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, list[str]]]:
    """Fake DNS on the running event loop: map host names to their addresses."""
    records: dict[str, list[str]] = {}

    async def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        if host not in records:
            raise socket.gaierror("not found")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in records[host]]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    yield records


async def test_hostnames_resolving_to_private_addresses_are_blocked(
    dns: dict[str, list[str]],
) -> None:
    dns["sneaky.example"] = ["93.184.215.14", "10.1.2.3"]  # one bad address is enough
    with pytest.raises(UnsafeURLError, match="non-public"):
        await resolve_allowed("sneaky.example", allow_private=False)
    assert len(await resolve_allowed("sneaky.example", allow_private=True)) == 2
    with pytest.raises(UnsafeURLError, match="resolve"):
        await resolve_allowed("missing.example", allow_private=False)


async def test_guarded_transport_refuses_to_connect_to_loopback() -> None:
    # A real connection attempt: the guard must stop it before any socket opens.
    async with httpx2.AsyncClient(transport=guarded_transport(allow_private=False)) as client:
        with pytest.raises(UnsafeURLError):
            await client.get("http://127.0.0.1:9/")
        with pytest.raises(UnsafeURLError):
            await client.get("http://[::1]:9/")


async def test_guarded_transport_allows_private_hosts_when_configured() -> None:
    # Allowed through the guard, so the failure is an ordinary connection error
    # (refused on Linux and macOS; Windows reports a timeout instead).
    async with httpx2.AsyncClient(
        transport=guarded_transport(allow_private=True), timeout=2.0
    ) as client:
        with pytest.raises((httpx2.ConnectError, httpx2.ConnectTimeout)):
            await client.get("http://127.0.0.1:9/")
