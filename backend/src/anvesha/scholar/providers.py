"""Clients for free scholarly search APIs. Each returns papers in one common shape."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element

import httpx2
from defusedxml import ElementTree
from pydantic import SecretStr

from anvesha.config import Settings
from anvesha.errors import UpstreamError

YearRange = tuple[int | None, int | None]
_TAGS = re.compile(r"<[^>]+>")
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
MAX_AUTHORS = 20
MAX_ABSTRACT_CHARS = 4000


@dataclass(frozen=True)
class Paper:
    source: str  # which API found it
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    published: str | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None  # landing page
    pdf_url: str | None = None  # open-access full text, when known
    abstract: str | None = None
    citation_count: int | None = None
    external_id: str | None = None  # the API's own id (OpenAlex W..., arXiv id, PMID)


class ScholarError(UpstreamError):
    code = "scholar_search_failed"


ScholarFunction = Callable[
    [httpx2.AsyncClient, str, int, YearRange, Settings], Awaitable[list[Paper]]
]


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value else None


def _text(value: Any, limit: int = 1000) -> str | None:
    if not value:
        return None
    cleaned = " ".join(_TAGS.sub(" ", str(value)).split())
    return cleaned[:limit] or None


def _clean_doi(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", value.strip(), flags=re.I)
    return doi.lower() or None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _get(
    http: httpx2.AsyncClient,
    name: str,
    url: str,
    settings: Settings,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    request_headers = {"User-Agent": settings.api_user_agent}
    request_headers.update(headers or {})
    try:
        response = await http.get(
            url,
            params={k: v for k, v in params.items() if v is not None},
            headers=request_headers,
            timeout=settings.search_timeout_seconds,
        )
    except httpx2.TimeoutException as exc:
        raise ScholarError(f"{name} timed out.", code="scholar_timeout", http_status=504) from exc
    except httpx2.HTTPError as exc:
        raise ScholarError(f"{name} is unreachable ({exc.__class__.__name__}).") from exc
    if response.status_code == 429:
        raise ScholarError(
            f"{name} rate limit reached; try again shortly.",
            code="scholar_rate_limited",
            http_status=429,
        )
    if response.status_code >= 300:
        # APIs don't redirect normal requests; NCBI, for one, redirects to a "misuse" page.
        raise ScholarError(
            f"{name} returned HTTP {response.status_code}.",
            retryable=response.status_code >= 500,
        )
    return response


def _json(response: httpx2.Response, name: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ScholarError(f"{name} returned a response that isn't JSON.") from exc


def _xml(response: httpx2.Response, name: str) -> Element:
    try:
        root: Element = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        raise ScholarError(f"{name} returned malformed XML.") from exc
    return root


def _openalex_abstract(inverted: Any) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]}; rebuild the text."""
    if not isinstance(inverted, dict):
        return None
    positions: dict[int, str] = {}
    for word, indexes in inverted.items():
        for index in indexes if isinstance(indexes, list) else []:
            if isinstance(index, int):
                positions[index] = word
    return _text(" ".join(positions[i] for i in sorted(positions)), MAX_ABSTRACT_CHARS)


async def search_openalex(
    http: httpx2.AsyncClient, query: str, limit: int, years: YearRange, settings: Settings
) -> list[Paper]:
    year_from, year_to = years
    year_filter = None
    if year_from or year_to:
        year_filter = f"publication_year:{year_from or ''}-{year_to or ''}"
    response = await _get(
        http,
        "OpenAlex",
        "https://api.openalex.org/works",
        settings,
        params={
            "search": query,
            "per-page": limit,
            "filter": year_filter,
            "mailto": settings.scholar_contact_email,
            "api_key": _secret(settings.openalex_api_key),
            "select": "id,doi,display_name,publication_year,publication_date,authorships,"
            "primary_location,best_oa_location,open_access,cited_by_count,"
            "abstract_inverted_index",
        },
    )
    papers = []
    for item in _json(response, "OpenAlex").get("results", []):
        if not isinstance(item, dict) or not item.get("display_name"):
            continue
        primary = item.get("primary_location") or {}
        best_oa = item.get("best_oa_location") or {}
        papers.append(
            Paper(
                source="openalex",
                title=_text(item["display_name"], 1000) or "",
                authors=[
                    name
                    for a in item.get("authorships") or []
                    if (name := _text((a.get("author") or {}).get("display_name"), 200))
                ][:MAX_AUTHORS],
                year=_int(item.get("publication_year")),
                published=_text(item.get("publication_date"), 32),
                venue=_text((primary.get("source") or {}).get("display_name"), 500),
                doi=_clean_doi(item.get("doi")),
                url=primary.get("landing_page_url") or item.get("doi") or item.get("id"),
                pdf_url=best_oa.get("pdf_url") or (item.get("open_access") or {}).get("oa_url"),
                abstract=_openalex_abstract(item.get("abstract_inverted_index")),
                citation_count=_int(item.get("cited_by_count")),
                external_id=str(item.get("id", "")).rsplit("/", 1)[-1] or None,
            )
        )
    return papers


async def search_semantic_scholar(
    http: httpx2.AsyncClient, query: str, limit: int, years: YearRange, settings: Settings
) -> list[Paper]:
    year_from, year_to = years
    key = _secret(settings.semantic_scholar_api_key)
    response = await _get(
        http,
        "Semantic Scholar",
        "https://api.semanticscholar.org/graph/v1/paper/search",
        settings,
        params={
            "query": query,
            "limit": limit,
            "year": f"{year_from or ''}-{year_to or ''}" if year_from or year_to else None,
            "fields": "title,authors,year,abstract,externalIds,url,openAccessPdf,venue,"
            "citationCount,publicationDate",
        },
        headers={"x-api-key": key} if key else None,
    )
    papers = []
    for item in _json(response, "Semantic Scholar").get("data") or []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        external = item.get("externalIds") or {}
        papers.append(
            Paper(
                source="semantic_scholar",
                title=_text(item["title"], 1000) or "",
                authors=[
                    name for a in item.get("authors") or [] if (name := _text(a.get("name"), 200))
                ][:MAX_AUTHORS],
                year=_int(item.get("year")),
                published=_text(item.get("publicationDate"), 32),
                venue=_text(item.get("venue"), 500),
                doi=_clean_doi(external.get("DOI")),
                url=item.get("url"),
                pdf_url=(item.get("openAccessPdf") or {}).get("url") or None,
                abstract=_text(item.get("abstract"), MAX_ABSTRACT_CHARS),
                citation_count=_int(item.get("citationCount")),
                external_id=item.get("paperId"),
            )
        )
    return papers


def _arxiv_query(query: str) -> str:
    terms = [t for t in re.findall(r"\w+", query) if len(t) > 2][:8] or [query.strip()]
    return " AND ".join(f"all:{term}" for term in terms)


async def search_arxiv(
    http: httpx2.AsyncClient, query: str, limit: int, years: YearRange, settings: Settings
) -> list[Paper]:
    response = await _get(
        http,
        "arXiv",
        "https://export.arxiv.org/api/query",
        settings,
        params={
            "search_query": _arxiv_query(query),
            "start": 0,
            # Ask for extra results when filtering by year afterwards.
            "max_results": limit * 3 if any(years) else limit,
            "sortBy": "relevance",
        },
    )
    year_from, year_to = years
    papers = []
    for entry in _xml(response, "arXiv").iter(f"{_ATOM}entry"):
        title = _text(entry.findtext(f"{_ATOM}title"), 1000)
        abs_url = (entry.findtext(f"{_ATOM}id") or "").strip()
        if not title or not abs_url:
            continue
        published = (entry.findtext(f"{_ATOM}published") or "")[:10] or None
        year = _int(published[:4]) if published else None
        if year and ((year_from and year < year_from) or (year_to and year > year_to)):
            continue
        pdf_url = next(
            (
                link.get("href")
                for link in entry.findall(f"{_ATOM}link")
                if link.get("title") == "pdf" or link.get("type") == "application/pdf"
            ),
            None,
        )
        papers.append(
            Paper(
                source="arxiv",
                title=title,
                authors=[
                    name
                    for author in entry.findall(f"{_ATOM}author")
                    if (name := _text(author.findtext(f"{_ATOM}name"), 200))
                ][:MAX_AUTHORS],
                year=year,
                published=published,
                venue=_text(entry.findtext(f"{_ARXIV}journal_ref"), 500) or "arXiv",
                doi=_clean_doi(entry.findtext(f"{_ARXIV}doi")),
                url=abs_url.replace("http://", "https://", 1),
                pdf_url=pdf_url.replace("http://", "https://", 1) if pdf_url else None,
                abstract=_text(entry.findtext(f"{_ATOM}summary"), MAX_ABSTRACT_CHARS),
                external_id=abs_url.rsplit("/abs/", 1)[-1] or None,
            )
        )
    return papers[:limit]


async def search_crossref(
    http: httpx2.AsyncClient, query: str, limit: int, years: YearRange, settings: Settings
) -> list[Paper]:
    year_from, year_to = years
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    response = await _get(
        http,
        "Crossref",
        "https://api.crossref.org/works",
        settings,
        params={
            "query": query,
            "rows": limit,
            "filter": ",".join(filters) or None,
            "mailto": settings.scholar_contact_email,
            "select": "DOI,title,author,issued,container-title,URL,abstract,"
            "is-referenced-by-count,link",
        },
    )
    papers = []
    for item in (_json(response, "Crossref").get("message") or {}).get("items") or []:
        if not isinstance(item, dict) or not (titles := item.get("title")):
            continue
        date_parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
        authors = []
        for author in item.get("author") or []:
            name = author.get("name") or " ".join(
                part for part in (author.get("given"), author.get("family")) if part
            )
            if cleaned := _text(name, 200):
                authors.append(cleaned)
        pdf_url = next(
            (
                link.get("URL")
                for link in item.get("link") or []
                if link.get("content-type") == "application/pdf"
            ),
            None,
        )
        papers.append(
            Paper(
                source="crossref",
                title=_text(titles[0], 1000) or "",
                authors=authors[:MAX_AUTHORS],
                year=_int(date_parts[0]),
                published="-".join(f"{int(p):02d}" for p in date_parts if p is not None) or None,
                venue=_text((item.get("container-title") or [None])[0], 500),
                doi=_clean_doi(item.get("DOI")),
                url=item.get("URL"),
                pdf_url=pdf_url,
                abstract=_text(item.get("abstract"), MAX_ABSTRACT_CHARS),
                citation_count=_int(item.get("is-referenced-by-count")),
                external_id=_clean_doi(item.get("DOI")),
            )
        )
    return papers


def _all_text(element: Element | None) -> str:
    return "".join(element.itertext()) if element is not None else ""


async def search_pubmed(
    http: httpx2.AsyncClient, query: str, limit: int, years: YearRange, settings: Settings
) -> list[Paper]:
    year_from, year_to = years
    common = {
        "tool": "anvesha",
        "email": settings.scholar_contact_email,
        "api_key": _secret(settings.ncbi_api_key),
    }
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    date_params: dict[str, Any] = {}
    if year_from or year_to:
        date_params = {
            "datetype": "pdat",
            "mindate": str(year_from or 1800),
            "maxdate": str(year_to or 3000),
        }
    search = await _get(
        http,
        "PubMed",
        f"{base}/esearch.fcgi",
        settings,
        params={
            "db": "pubmed",
            "term": query,
            "retmax": limit,
            "retmode": "json",
            "sort": "relevance",
            **date_params,
            **common,
        },
    )
    ids = [
        i
        for i in (_json(search, "PubMed").get("esearchresult") or {}).get("idlist") or []
        if str(i).isdigit()
    ]
    if not ids:
        return []
    fetched = await _get(
        http,
        "PubMed",
        f"{base}/efetch.fcgi",
        settings,
        params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml", **common},
    )
    papers = []
    for article in _xml(fetched, "PubMed").iter("PubmedArticle"):
        citation = article.find("MedlineCitation")
        info = citation.find("Article") if citation is not None else None
        if citation is None or info is None:
            continue
        pmid = (citation.findtext("PMID") or "").strip()
        title = _text(_all_text(info.find("ArticleTitle")), 1000)
        if not title:
            continue
        abstract_parts = []
        for part in info.findall("Abstract/AbstractText"):
            label = part.get("Label")
            text = _all_text(part).strip()
            abstract_parts.append(f"{label}: {text}" if label else text)
        authors = []
        for author in info.findall("AuthorList/Author"):
            name = author.findtext("CollectiveName") or " ".join(
                p for p in (author.findtext("ForeName"), author.findtext("LastName")) if p
            )
            if cleaned := _text(name, 200):
                authors.append(cleaned)
        pub_date = info.find("Journal/JournalIssue/PubDate")
        year_text = pub_date.findtext("Year") if pub_date is not None else None
        if not year_text and pub_date is not None:
            year_text = (pub_date.findtext("MedlineDate") or "")[:4]
        doi = next(
            (
                _all_text(node)
                for node in article.findall("PubmedData/ArticleIdList/ArticleId")
                if node.get("IdType") == "doi"
            ),
            None,
        )
        papers.append(
            Paper(
                source="pubmed",
                title=title,
                authors=authors[:MAX_AUTHORS],
                year=_int(year_text),
                published=year_text or None,
                venue=_text(info.findtext("Journal/Title"), 500),
                doi=_clean_doi(doi),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                abstract=_text(" ".join(abstract_parts), MAX_ABSTRACT_CHARS),
                external_id=pmid or None,
            )
        )
    return papers


@dataclass(frozen=True)
class ScholarSourceSpec:
    id: str
    name: str
    search: ScholarFunction
    description: str


SCHOLAR_SOURCES: dict[str, ScholarSourceSpec] = {
    spec.id: spec
    for spec in (
        ScholarSourceSpec(
            "openalex",
            "OpenAlex",
            search_openalex,
            "250M+ works across every field, with open-access links.",
        ),
        ScholarSourceSpec(
            "semantic_scholar",
            "Semantic Scholar",
            search_semantic_scholar,
            "AI-ranked papers with citation counts. Rate-limited without an API key.",
        ),
        ScholarSourceSpec(
            "arxiv", "arXiv", search_arxiv, "Preprints in physics, maths, CS, biology and more."
        ),
        ScholarSourceSpec(
            "crossref", "Crossref", search_crossref, "DOI metadata for published literature."
        ),
        ScholarSourceSpec(
            "pubmed", "PubMed", search_pubmed, "Biomedical and life-science literature."
        ),
    )
}
DEFAULT_SCHOLAR_SOURCES = ("openalex", "semantic_scholar", "arxiv")
