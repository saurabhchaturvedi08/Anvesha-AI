"""An HTTP transport that refuses to connect to private or internal addresses.

The check runs when each TCP connection opens, after DNS resolution, and the connection
goes to the exact address that was checked. That closes the usual SSRF gaps: redirects
to internal hosts, DNS names that resolve to private addresses, and DNS rebinding
(answering differently on a second lookup). TLS still verifies the original host name,
because httpcore takes the server name from the URL, not from the connected address.
"""

from collections.abc import Iterable

import httpcore2
import httpx2

from anvesha.net import UnsafeURLError, resolve_allowed

SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


class GuardedNetworkBackend(httpcore2.AsyncNetworkBackend):
    def __init__(
        self, *, allow_private: bool, inner: httpcore2.AsyncNetworkBackend | None = None
    ) -> None:
        self._allow_private = allow_private
        self._inner = inner or httpcore2.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        addresses = await resolve_allowed(host, allow_private=self._allow_private)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        raise UnsafeURLError("Unix sockets are not allowed.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_transport(*, allow_private: bool) -> httpx2.AsyncHTTPTransport:
    """A transport whose every connection passes the address check. Ignores proxy env vars."""
    transport = httpx2.AsyncHTTPTransport(trust_env=False, retries=0)
    pool = transport._pool
    if not isinstance(pool, httpcore2.AsyncConnectionPool):  # pragma: no cover
        raise RuntimeError("Unexpected httpx2 transport internals; update guarded_transport().")
    pool._network_backend = GuardedNetworkBackend(allow_private=allow_private)
    return transport
