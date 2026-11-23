"""Research tools: search the web and the literature, read sources, find and verify quotes."""

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from anvesha.db.models import Source
from anvesha.ingest.extract import page_for_offset
from anvesha.scholar.providers import SCHOLAR_SOURCES
from anvesha.scholar.service import scholar_search
from anvesha.search.service import web_search
from anvesha.sources.service import get_source, read_text
from anvesha.tools.base import Tool, ToolContext, ToolRegistry, ToolResult, untrusted
from anvesha.verify.quotes import context_around

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
PREVIEW_CHARS = 4000


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebSearchArgs(_Args):
    query: Query = Field(description="What to search for. Specific queries work best.")
    limit: int = Field(default=8, ge=1, le=20, description="Number of results.")
    provider: str = Field(
        default="", description="Search provider id. Leave empty for the default."
    )


async def _web_search(ctx: ToolContext, args: WebSearchArgs) -> ToolResult:
    response = await web_search(
        db=ctx.db,
        http=ctx.http,
        settings=ctx.settings,
        vault=ctx.vault,
        workspace_id=ctx.workspace_id,
        query=args.query,
        limit=args.limit,
        provider=args.provider or None,
    )
    if not response.results:
        return ToolResult(f'No web results for "{args.query}".', {"results": []})
    lines = []
    for index, result in enumerate(response.results, start=1):
        lines.append(f"[{index}] {result.title or '(untitled)'}\nURL: {result.url}")
        if result.published:
            lines.append(f"Published: {result.published}")
        if result.snippet:
            lines.append(f"Snippet: {result.snippet}")
        lines.append("")
    content = (
        f'Web results for "{args.query}" (via {response.provider}). Snippets are not '
        "evidence: fetch a page before quoting it.\n"
        + untrusted("web_search", "\n".join(lines).strip())
    )
    return ToolResult(
        content,
        {
            "provider": response.provider,
            "results": [result.__dict__ for result in response.results],
        },
    )


class ScholarSearchArgs(_Args):
    query: Query = Field(description="Topic, title words, or keywords.")
    sources: list[str] = Field(
        default_factory=list,
        description=f"Any of: {', '.join(SCHOLAR_SOURCES)}. Empty: OpenAlex, Semantic "
        "Scholar and arXiv.",
    )
    limit: int = Field(default=10, ge=1, le=25)
    year_from: int = Field(default=0, ge=0, le=2100, description="0 for no lower bound.")
    year_to: int = Field(default=0, ge=0, le=2100, description="0 for no upper bound.")


async def _scholar_search(ctx: ToolContext, args: ScholarSearchArgs) -> ToolResult:
    response = await scholar_search(
        http=ctx.http,
        settings=ctx.settings,
        query=args.query,
        sources=args.sources or None,
        limit=args.limit,
        years=(args.year_from or None, args.year_to or None),
    )
    lines = []
    for index, paper in enumerate(response.papers, start=1):
        authors = ", ".join(paper.authors[:4]) + (" et al." if len(paper.authors) > 4 else "")
        lines.append(f"[{index}] {paper.title} ({paper.year or 'n.d.'})")
        if authors:
            lines.append(f"Authors: {authors}")
        if paper.venue:
            lines.append(f"Venue: {paper.venue}")
        if paper.doi:
            lines.append(f"DOI: {paper.doi}")
        if paper.url:
            lines.append(f"URL: {paper.url}")
        if paper.pdf_url:
            lines.append(f"Open-access PDF: {paper.pdf_url}")
        if paper.citation_count is not None:
            lines.append(f"Citations: {paper.citation_count}")
        if paper.abstract:
            lines.append(f"Abstract: {paper.abstract[:1200]}")
        lines.append("")
    header = f'Scholarly results for "{args.query}": {len(response.papers)} paper(s).'
    if response.errors:
        header += " Unavailable: " + "; ".join(f"{k} ({v})" for k, v in response.errors.items())
    body = untrusted("scholar_search", "\n".join(lines).strip()) if lines else "No papers found."
    return ToolResult(
        f"{header}\nAbstracts are not evidence: fetch the paper before quoting it.\n{body}",
        {"papers": [paper.__dict__ for paper in response.papers], "errors": response.errors},
    )


class FetchPageArgs(_Args):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


async def _fetch_page(ctx: ToolContext, args: FetchPageArgs) -> ToolResult:
    result = await ctx.sources.add_from_url(
        ctx.workspace_id,
        ctx.project_id,
        args.url,
        added_by=ctx.added_by,
        respect_robots=ctx.settings.fetch_respect_robots_txt and ctx.added_by == "agent",
    )
    source = result.source
    preview, next_offset = read_text(source, 0, PREVIEW_CHARS)
    facts = [f"{source.source_type}, {source.char_count:,} characters"]
    if pages := source.details.get("page_count"):
        facts.append(f"{pages} pages")
    lines = [
        f"{'Saved' if result.created else 'Already saved'} as source {source.id}: "
        f"{source.title or '(untitled)'}",
        f"URL: {source.url}",
        f"Type: {', '.join(facts)}",
    ]
    if source.published:
        lines.append(f"Published: {source.published}")
    lines.append(untrusted(str(source.id), preview))
    if next_offset is not None:
        lines.append(
            f"Showing the first {len(preview):,} characters. Continue with read_source "
            f"(offset={next_offset}) or find passages with search_sources."
        )
    return ToolResult("\n".join(lines), _source_summary(source) | {"created": result.created})


class ReadSourceArgs(_Args):
    source_id: uuid.UUID
    offset: int = Field(default=0, ge=0, description="Character offset to start reading from.")
    max_chars: int = Field(default=6000, ge=200, le=20000)


async def _read_source(ctx: ToolContext, args: ReadSourceArgs) -> ToolResult:
    async with ctx.db.session() as session:
        source = await get_source(session, ctx.workspace_id, ctx.project_id, args.source_id)
    text, next_offset = read_text(source, args.offset, args.max_chars)
    end = args.offset + len(text)
    location = f"characters {args.offset:,}-{end:,} of {source.char_count:,}"
    if (page := page_for_offset(source.details, args.offset)) is not None:
        location += f", starting on page {page}"
    footer = f"Continue with offset={next_offset}." if next_offset is not None else "End of source."
    return ToolResult(
        f"Source {source.id}: {source.title or '(untitled)'} ({location})\n"
        f"{untrusted(str(source.id), text)}\n{footer}",
        {"source_id": str(source.id), "offset": args.offset, "next_offset": next_offset},
    )


class SearchSourcesArgs(_Args):
    query: Query = Field(description="What to look for in this project's saved sources.")
    limit: int = Field(default=8, ge=1, le=20)
    source_ids: list[uuid.UUID] = Field(
        default_factory=list, description="Only search these sources. Empty: all sources."
    )


async def _search_sources(ctx: ToolContext, args: SearchSourcesArgs) -> ToolResult:
    outcome = await ctx.sources.search(
        ctx.workspace_id,
        ctx.project_id,
        args.query,
        limit=args.limit,
        source_ids=args.source_ids or None,
    )
    if not outcome.hits:
        message = f'No saved passages match "{args.query}".'
        if outcome.semantic_note:
            message += f" ({outcome.semantic_note})"
        return ToolResult(message, {"hits": [], "mode": outcome.mode})
    lines = []
    for index, hit in enumerate(outcome.hits, start=1):
        lines.append(
            f"[{index}] source {hit.source_id} ({hit.source_title or 'untitled'}), "
            f"characters {hit.char_start:,}-{hit.char_end:,}\n{hit.text}\n"
        )
    header = f'{len(outcome.hits)} passage(s) for "{args.query}" ({outcome.mode} search).'
    if outcome.semantic_note:
        header += f" {outcome.semantic_note}"
    return ToolResult(
        f"{header}\n{untrusted('search_sources', chr(10).join(lines).strip())}",
        {
            "mode": outcome.mode,
            "hits": [hit.__dict__ | {"source_id": str(hit.source_id)} for hit in outcome.hits],
        },
    )


class VerifyQuoteArgs(_Args):
    source_id: uuid.UUID
    quote: Annotated[str, StringConstraints(min_length=1, max_length=5000)] = Field(
        description="The exact passage, copied from the source. Use ... to skip words."
    )


async def _verify_quote(ctx: ToolContext, args: VerifyQuoteArgs) -> ToolResult:
    source, match = await ctx.sources.verify_quote(
        ctx.workspace_id, ctx.project_id, args.source_id, args.quote
    )
    data: dict[str, object] = {
        "source_id": str(source.id),
        "status": match.status,
        "supported": match.supported,
        "spans": match.spans,
    }
    if match.status == "too_short":
        return ToolResult(
            "Quote is too short to verify. Quote at least a full clause (4+ words, 20+ "
            "characters), and each part around an ellipsis needs 12+ characters.",
            data,
        )
    if not match.supported:
        return ToolResult(
            f"NOT FOUND: the quote does not appear in source {source.id}. Copy the passage "
            "exactly (read_source or search_sources show the text); don't paraphrase.",
            data,
        )
    page = page_for_offset(source.details, match.spans[0][0])
    data["page"] = page
    where = f" (page {page})" if page else ""
    return ToolResult(
        f"VERIFIED ({match.status}) in source {source.id}{where}. Context:\n"
        + untrusted(str(source.id), context_around(source.text, match.spans[0])),
        data,
    )


class ListSourcesArgs(_Args):
    limit: int = Field(default=50, ge=1, le=200)


async def _list_sources(ctx: ToolContext, args: ListSourcesArgs) -> ToolResult:
    async with ctx.db.session() as session:
        sources = (
            await session.scalars(
                select(Source)
                .where(Source.workspace_id == ctx.workspace_id, Source.project_id == ctx.project_id)
                .order_by(Source.created_at)
                .limit(args.limit)
            )
        ).all()
    if not sources:
        return ToolResult("No sources saved in this project yet.", {"sources": []})
    lines = [
        f"- {s.id}: {s.title or '(untitled)'} [{s.source_type}, {s.char_count:,} chars]"
        + (f" {s.url}" if s.url else f" (uploaded file {s.filename})")
        for s in sources
    ]
    return ToolResult(
        f"{len(sources)} saved source(s):\n" + untrusted("list_sources", "\n".join(lines)),
        {"sources": [_source_summary(s) for s in sources]},
    )


def _source_summary(source: Source) -> dict[str, object]:
    return {
        "source_id": str(source.id),
        "title": source.title,
        "url": source.url,
        "source_type": source.source_type,
        "char_count": source.char_count,
        "embedding_status": source.embedding_status,
    }


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        Tool(
            "web_search",
            "Search the web. Returns titles, URLs and snippets. Snippets are leads, not "
            "evidence: use fetch_page to read a page before relying on it.",
            WebSearchArgs,
            _web_search,
        ),
        Tool(
            "scholar_search",
            "Search scholarly literature (OpenAlex, Semantic Scholar, arXiv, Crossref, "
            "PubMed). Returns papers with abstracts, DOIs and open-access PDF links.",
            ScholarSearchArgs,
            _scholar_search,
        ),
        Tool(
            "fetch_page",
            "Download a web page or PDF, extract its text, and save it as a project source. "
            "Returns the source id and the beginning of the text.",
            FetchPageArgs,
            _fetch_page,
        ),
        Tool(
            "read_source",
            "Read a saved source's text from a character offset, page by page.",
            ReadSourceArgs,
            _read_source,
        ),
        Tool(
            "search_sources",
            "Find the passages most relevant to a question across this project's saved "
            "sources (keyword plus semantic search).",
            SearchSourcesArgs,
            _search_sources,
        ),
        Tool(
            "verify_quote",
            "Check that a quote appears word for word in a saved source. Every quote used as "
            "evidence must pass this check.",
            VerifyQuoteArgs,
            _verify_quote,
        ),
        Tool(
            "list_sources",
            "List the sources saved in this project.",
            ListSourcesArgs,
            _list_sources,
        ),
    ):
        registry.register(tool)
    return registry
