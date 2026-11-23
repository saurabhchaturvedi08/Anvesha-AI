from collections.abc import AsyncIterator

import httpx2
import pytest

from anvesha.errors import InvalidInputError
from anvesha.scholar.providers import Paper
from anvesha.scholar.service import merge_papers, scholar_search
from anvesha.search.providers import SEARCH_PROVIDERS, SearchAccess, SearchError, run_search
from tests.conftest import FakeProviderAPI, make_settings

SETTINGS = make_settings(scholar_contact_email="ops@example.org")


@pytest.fixture
def api() -> FakeProviderAPI:
    return FakeProviderAPI()


@pytest.fixture
async def http(api: FakeProviderAPI) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(api.handler)) as client:
        yield client


def _access(provider: str, base: str, key: str | None = "key-123") -> SearchAccess:
    return SearchAccess(provider, base, key, "deployment")


# --- Web search ------------------------------------------------------------------------


async def test_searxng_results_are_parsed_and_deduplicated(
    http: httpx2.AsyncClient, api: FakeProviderAPI
) -> None:
    api.add(
        "http://searxng:8080/search",
        200,
        {
            "results": [
                {
                    "title": " Solar  report ",
                    "url": "https://a.example/1",
                    "content": "Grew 24%",
                    "engine": "bing",
                    "publishedDate": "2025-03-01",
                },
                {"title": "Duplicate", "url": "https://a.example/1", "content": "again"},
                {"title": "Not a web URL", "url": "javascript:alert(1)"},
                {"title": "No URL"},
            ]
        },
    )
    results = await run_search(
        http,
        SEARCH_PROVIDERS["searxng"],
        _access("searxng", "http://searxng:8080", None),
        "solar",
        8,
        SETTINGS,
    )
    assert [(r.title, r.url, r.snippet, r.engine) for r in results] == [
        ("Solar report", "https://a.example/1", "Grew 24%", "bing")
    ]
    request = api.requests[0]
    assert request.url.params["format"] == "json" and request.url.params["q"] == "solar"


@pytest.mark.parametrize(
    ("provider", "base", "url", "body", "auth_header"),
    [
        (
            "tavily",
            "https://api.tavily.com",
            "https://api.tavily.com/search",
            {"results": [{"title": "T", "url": "https://t.example", "content": "c"}]},
            ("authorization", "Bearer key-123"),
        ),
        (
            "brave",
            "https://api.search.brave.com/res/v1",
            "https://api.search.brave.com/res/v1/web/search",
            {"web": {"results": [{"title": "T", "url": "https://t.example", "description": "c"}]}},
            ("x-subscription-token", "key-123"),
        ),
        (
            "exa",
            "https://api.exa.ai",
            "https://api.exa.ai/search",
            {"results": [{"title": "T", "url": "https://t.example", "text": "c"}]},
            ("x-api-key", "key-123"),
        ),
    ],
)
async def test_paid_search_apis_send_their_key_and_parse_results(
    http: httpx2.AsyncClient,
    api: FakeProviderAPI,
    provider: str,
    base: str,
    url: str,
    body: dict[str, object],
    auth_header: tuple[str, str],
) -> None:
    api.add(url, 200, body)
    results = await run_search(
        http, SEARCH_PROVIDERS[provider], _access(provider, base), "q", 5, SETTINGS
    )
    assert [(r.title, r.url, r.snippet) for r in results] == [("T", "https://t.example", "c")]
    assert api.requests[0].headers[auth_header[0]] == auth_header[1]


@pytest.mark.parametrize(
    ("status_code", "code", "http_status"),
    [
        (401, "search_auth_failed", 502),
        (429, "search_rate_limited", 429),
        (500, "search_failed", 502),
    ],
)
async def test_search_errors_are_classified(
    http: httpx2.AsyncClient, api: FakeProviderAPI, status_code: int, code: str, http_status: int
) -> None:
    api.add("https://api.tavily.com/search", status_code, {"detail": "x"})
    with pytest.raises(SearchError) as excinfo:
        await run_search(
            http,
            SEARCH_PROVIDERS["tavily"],
            _access("tavily", "https://api.tavily.com"),
            "q",
            5,
            SETTINGS,
        )
    assert (excinfo.value.code, excinfo.value.http_status) == (code, http_status)


async def test_unreachable_search_provider(http: httpx2.AsyncClient) -> None:
    with pytest.raises(SearchError) as excinfo:
        await run_search(
            http,
            SEARCH_PROVIDERS["searxng"],
            _access("searxng", "http://down:8080", None),
            "q",
            5,
            SETTINGS,
        )
    assert excinfo.value.code == "search_unreachable"


# --- Scholarly sources ---------------------------------------------------------------

OPENALEX = {
    "results": [
        {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1000/ABC",
            "display_name": "Solar Growth in <i>India</i>",
            "publication_year": 2024,
            "publication_date": "2024-05-01",
            "authorships": [{"author": {"display_name": "Asha Rao"}}],
            "primary_location": {
                "landing_page_url": "https://journal.example/abc",
                "source": {"display_name": "Energy Journal"},
            },
            "best_oa_location": {"pdf_url": "https://journal.example/abc.pdf"},
            "cited_by_count": 42,
            "abstract_inverted_index": {"Solar": [0], "grew": [1], "fast.": [2]},
        }
    ]
}

SEMANTIC_SCHOLAR = {
    "data": [
        {
            "paperId": "s2-1",
            "title": "Solar growth in India",  # same paper as OpenAlex, different case
            "authors": [{"name": "Asha Rao"}],
            "year": 2024,
            "externalIds": {"DOI": "10.1000/abc"},
            "url": "https://www.semanticscholar.org/paper/s2-1",
            "openAccessPdf": None,
            "venue": "Energy J.",
            "citationCount": 40,
            "abstract": None,
        },
        {"paperId": "s2-2", "title": "Wind power review", "year": 2021, "authors": []},
    ]
}

ARXIV = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <published>2024-01-02T00:00:00Z</published>
    <title>Grid storage
      for solar</title>
    <summary>We study batteries.</summary>
    <author><name>Lee Chen</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v2" type="application/pdf"/>
    <arxiv:doi>10.2000/XYZ</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1901.00001v1</id>
    <published>2019-01-02T00:00:00Z</published>
    <title>Old paper</title>
  </entry>
</feed>"""

CROSSREF = {
    "message": {
        "items": [
            {
                "DOI": "10.3000/QQ",
                "title": ["Perovskite cells"],
                "author": [{"given": "Mia", "family": "Diaz"}, {"name": "Solar Consortium"}],
                "issued": {"date-parts": [[2023, 7]]},
                "container-title": ["Nature Energy"],
                "URL": "https://doi.org/10.3000/qq",
                "abstract": "<jats:p>Efficiency rose.</jats:p>",
                "is-referenced-by-count": 7,
            },
            {"DOI": "10.3000/notitle"},
        ]
    }
}

PUBMED_SEARCH = {"esearchresult": {"idlist": ["111", "bad-id"]}}
PUBMED_FETCH = b"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle>
  <MedlineCitation><PMID>111</PMID><Article>
    <Journal><JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue>
      <Title>Lancet</Title></Journal>
    <ArticleTitle>Heat <i>stress</i> and health</ArticleTitle>
    <Abstract><AbstractText Label="METHODS">We measured.</AbstractText>
      <AbstractText Label="RESULTS">It rose.</AbstractText></Abstract>
    <AuthorList><Author><LastName>Okafor</LastName><ForeName>Ada</ForeName></Author></AuthorList>
  </Article></MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="doi">10.4000/HS</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""


def _add_all(api: FakeProviderAPI) -> None:
    api.add("https://api.openalex.org/works", 200, OPENALEX)
    api.add("https://api.semanticscholar.org/graph/v1/paper/search", 200, SEMANTIC_SCHOLAR)
    api.add("https://export.arxiv.org/api/query", 200, ARXIV)
    api.add("https://api.crossref.org/works", 200, CROSSREF)
    api.add("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", 200, PUBMED_SEARCH)
    api.add("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", 200, PUBMED_FETCH)


async def test_every_scholarly_source_is_parsed(
    http: httpx2.AsyncClient, api: FakeProviderAPI
) -> None:
    _add_all(api)
    response = await scholar_search(
        http=http,
        settings=SETTINGS,
        query="solar",
        sources=["openalex", "semantic_scholar", "arxiv", "crossref", "pubmed"],
        limit=10,
        years=(2020, None),
    )
    assert response.errors == {}
    by_source = {paper.source: paper for paper in response.papers}

    openalex = by_source["openalex"]
    assert openalex.title == "Solar Growth in India"
    assert (openalex.doi, openalex.year, openalex.citation_count) == ("10.1000/abc", 2024, 42)
    assert openalex.abstract == "Solar grew fast."
    assert openalex.pdf_url == "https://journal.example/abc.pdf"

    arxiv = by_source["arxiv"]
    assert (arxiv.title, arxiv.doi, arxiv.external_id) == (
        "Grid storage for solar",
        "10.2000/xyz",
        "2401.00001v2",
    )
    assert arxiv.pdf_url == "https://arxiv.org/pdf/2401.00001v2"
    assert all(paper.title != "Old paper" for paper in response.papers)  # year filter

    crossref = by_source["crossref"]
    assert crossref.authors == ["Mia Diaz", "Solar Consortium"]
    assert (crossref.year, crossref.published, crossref.abstract) == (
        2023,
        "2023-07",
        "Efficiency rose.",
    )

    pubmed = by_source["pubmed"]
    assert pubmed.title == "Heat stress and health"
    assert pubmed.abstract == "METHODS: We measured. RESULTS: It rose."
    assert (pubmed.doi, pubmed.url) == ("10.4000/hs", "https://pubmed.ncbi.nlm.nih.gov/111/")

    # Semantic Scholar's copy of the OpenAlex paper was merged, not repeated.
    assert sum(p.doi == "10.1000/abc" for p in response.papers) == 1
    assert "Wind power review" in [p.title for p in response.papers]

    params = {r.url.host: r.url.params for r in api.requests}
    assert params["api.openalex.org"]["mailto"] == "ops@example.org"
    assert params["api.openalex.org"]["filter"] == "publication_year:2020-"
    assert params["api.crossref.org"]["filter"] == "from-pub-date:2020"


async def test_failing_sources_are_reported_without_losing_the_rest(
    http: httpx2.AsyncClient, api: FakeProviderAPI
) -> None:
    api.add("https://api.openalex.org/works", 200, OPENALEX)
    api.add("https://api.semanticscholar.org/graph/v1/paper/search", 429, {})
    response = await scholar_search(
        http=http,
        settings=SETTINGS,
        query="solar",
        sources=["openalex", "semantic_scholar", "arxiv"],
    )
    assert [p.source for p in response.papers] == ["openalex"]
    assert set(response.errors) == {"semantic_scholar", "arxiv"}
    assert "rate limit" in response.errors["semantic_scholar"]


async def test_unknown_sources_and_bad_year_ranges_are_rejected(http: httpx2.AsyncClient) -> None:
    with pytest.raises(InvalidInputError):
        await scholar_search(http=http, settings=SETTINGS, query="q", sources=["google_scholar"])
    with pytest.raises(InvalidInputError):
        await scholar_search(http=http, settings=SETTINGS, query="q", years=(2024, 2020))


def test_merge_interleaves_and_fills_gaps() -> None:
    a = [Paper("a", "One", doi="10.1/x"), Paper("a", "Two")]
    b = [Paper("b", "ONE!", abstract="filled in", citation_count=3), Paper("b", "Three")]
    merged = merge_papers([a, b], limit=10)
    assert [p.title for p in merged] == ["One", "Two", "Three"]
    assert (merged[0].abstract, merged[0].citation_count) == ("filled in", 3)
    assert len(merge_papers([a, b], limit=2)) == 2


async def test_api_redirects_are_errors_not_results(
    http: httpx2.AsyncClient, api: FakeProviderAPI
) -> None:
    # NCBI answers suspected abuse with a 302 to an HTML page.
    api.add("https://eutils.ncbi.nlm.nih.gov/", 302, b"<html>moved</html>", "text/html")
    response = await scholar_search(http=http, settings=SETTINGS, query="q", sources=["pubmed"])
    assert response.papers == [] and "HTTP 302" in response.errors["pubmed"]
    assert api.requests[0].headers["user-agent"] == SETTINGS.api_user_agent
