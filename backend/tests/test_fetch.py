from collections.abc import AsyncIterator

import httpx2
import pytest

from anvesha.web.fetch import FetchError, SafeFetcher, normalize_url
from tests.conftest import FakeWeb, make_pdf, make_settings

HTML = "<html><body><p>Hello</p></body></html>"


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
async def fetcher(web: FakeWeb) -> AsyncIterator[SafeFetcher]:
    settings = make_settings(fetch_max_bytes=10_000, fetch_max_redirects=2)
    instance = SafeFetcher(settings, transport=httpx2.MockTransport(web.handler))
    yield instance
    await instance.aclose()


async def _error(fetcher: SafeFetcher, url: str, **kwargs: bool) -> FetchError:
    with pytest.raises(FetchError) as excinfo:
        await fetcher.fetch(url, **kwargs)
    return excinfo.value


async def test_fetches_html_and_reports_type_and_charset(
    fetcher: SafeFetcher, web: FakeWeb
) -> None:
    web.page("https://example.org/a", HTML, "text/html; charset=ISO-8859-1")
    resource = await fetcher.fetch("https://example.org/a")
    assert (resource.content_type, resource.charset) == ("text/html", "ISO-8859-1")
    assert resource.body == HTML.encode()
    assert resource.url == "https://example.org/a"


async def test_follows_redirects_up_to_the_limit(fetcher: SafeFetcher, web: FakeWeb) -> None:
    web.redirect("http://example.org/old", "https://example.org/new")
    web.redirect("https://example.org/new", "/final")
    web.page("https://example.org/final", HTML)
    resource = await fetcher.fetch("http://example.org/old")
    assert resource.url == "https://example.org/final"
    assert resource.requested_url == "http://example.org/old"

    web.redirect("https://example.org/loop", "https://example.org/loop")
    assert (await _error(fetcher, "https://example.org/loop")).code == "too_many_redirects"


async def test_redirects_to_other_schemes_are_blocked(fetcher: SafeFetcher, web: FakeWeb) -> None:
    web.redirect("https://example.org/r", "file:///etc/passwd")
    error = await _error(fetcher, "https://example.org/r")
    assert (error.code, error.http_status) == ("fetch_blocked", 422)


async def test_size_limit_applies_to_declared_and_streamed_bodies(
    fetcher: SafeFetcher, web: FakeWeb
) -> None:
    web.page("https://example.org/big", "x" * 20_000, "text/plain")
    assert (await _error(fetcher, "https://example.org/big")).code == "fetch_too_large"
    web.page(
        "https://example.org/liar",
        "x" * 20_000,
        "text/plain",
        headers={"content-length": "10"},
    )
    assert (await _error(fetcher, "https://example.org/liar")).code == "fetch_too_large"


async def test_unsupported_types_are_rejected_but_pdfs_are_sniffed(
    fetcher: SafeFetcher, web: FakeWeb
) -> None:
    web.page("https://example.org/img", b"\x89PNG....", "image/png")
    error = await _error(fetcher, "https://example.org/img")
    assert (error.code, error.retryable) == ("unsupported_content_type", False)

    web.page("https://example.org/paper", make_pdf(["hi"]), "application/octet-stream")
    assert (await fetcher.fetch("https://example.org/paper")).content_type == "application/pdf"


@pytest.mark.parametrize(
    ("status_code", "http_status", "retryable"), [(404, 422, False), (503, 502, True)]
)
async def test_http_errors_are_classified(
    fetcher: SafeFetcher, web: FakeWeb, status_code: int, http_status: int, retryable: bool
) -> None:
    web.page("https://example.org/x", "nope", "text/plain", status_code=status_code)
    error = await _error(fetcher, "https://example.org/x")
    assert (error.code, error.http_status, error.retryable) == (
        "http_error",
        http_status,
        retryable,
    )


async def test_unreachable_hosts_are_reported(fetcher: SafeFetcher) -> None:
    assert (await _error(fetcher, "https://nowhere.example/")).code == "fetch_failed"


async def test_robots_txt_is_honoured_when_asked(fetcher: SafeFetcher, web: FakeWeb) -> None:
    web.page(
        "https://example.org/robots.txt",
        "User-agent: AnveshaBot\nDisallow: /private\n",
        "text/plain",
    )
    web.page("https://example.org/private/page", HTML)
    web.page("https://example.org/public", HTML)
    error = await _error(fetcher, "https://example.org/private/page", respect_robots=True)
    assert error.code == "robots_disallowed"
    await fetcher.fetch("https://example.org/private/page", respect_robots=False)
    await fetcher.fetch("https://example.org/public", respect_robots=True)
    # robots.txt is fetched once per origin, then cached.
    assert web.requests.count("https://example.org/robots.txt") == 1


def test_normalize_url() -> None:
    assert normalize_url("HTTPS://Example.ORG:443/a?b=1#frag") == "https://example.org/a?b=1"
    assert normalize_url("http://example.org") == "http://example.org/"
    assert normalize_url("http://example.org:8080/x") == "http://example.org:8080/x"
