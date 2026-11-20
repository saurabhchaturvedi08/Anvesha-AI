import pytest

from anvesha.net import UnsafeURLError, ensure_public_url, require_http_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:8080",
        "http://10.0.0.5/v1",
        "http://192.168.1.10",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://[::1]/v1",
    ],
)
async def test_private_addresses_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_public_url(url)


async def test_public_address_is_allowed() -> None:
    await ensure_public_url("https://8.8.8.8/v1")


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "example.com", ""])
def test_non_http_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        require_http_url(url)
