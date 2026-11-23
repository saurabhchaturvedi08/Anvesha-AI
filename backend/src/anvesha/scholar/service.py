"""Search several scholarly sources at once and merge the results."""

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import httpx2

from anvesha.config import Settings
from anvesha.errors import AppError, InvalidInputError
from anvesha.scholar.providers import (
    DEFAULT_SCHOLAR_SOURCES,
    SCHOLAR_SOURCES,
    Paper,
    YearRange,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScholarSearchResponse:
    query: str
    papers: list[Paper]
    # Sources that failed, with the reason. The other sources' results are still returned.
    errors: dict[str, str] = field(default_factory=dict)


def _title_key(title: str) -> str:
    return re.sub(r"[^\w]+", " ", title.casefold()).strip()


def merge_papers(groups: Sequence[list[Paper]], limit: int) -> list[Paper]:
    """Interleave sources by rank and drop duplicates (same DOI or same title).

    When a paper appears twice, missing fields (abstract, PDF link, citations) are filled
    in from the duplicate.
    """
    merged: list[Paper] = []
    index_by_key: dict[str, int] = {}
    longest = max((len(group) for group in groups), default=0)
    for rank in range(longest):
        for group in groups:
            if rank >= len(group):
                continue
            paper = group[rank]
            keys = [f"title:{_title_key(paper.title)}"]
            if paper.doi:
                keys.append(f"doi:{paper.doi}")
            existing = next((index_by_key[k] for k in keys if k in index_by_key), None)
            if existing is None:
                index_by_key.update(dict.fromkeys(keys, len(merged)))
                merged.append(paper)
                continue
            current = merged[existing]
            merged[existing] = replace(
                current,
                doi=current.doi or paper.doi,
                abstract=current.abstract or paper.abstract,
                pdf_url=current.pdf_url or paper.pdf_url,
                citation_count=current.citation_count
                if current.citation_count is not None
                else paper.citation_count,
                venue=current.venue or paper.venue,
            )
            index_by_key.update(dict.fromkeys(keys, existing))
    return merged[:limit]


async def scholar_search(
    *,
    http: httpx2.AsyncClient,
    settings: Settings,
    query: str,
    sources: Sequence[str] | None = None,
    limit: int = 10,
    years: YearRange = (None, None),
) -> ScholarSearchResponse:
    chosen = list(dict.fromkeys(sources or DEFAULT_SCHOLAR_SOURCES))
    unknown = [source for source in chosen if source not in SCHOLAR_SOURCES]
    if unknown:
        raise InvalidInputError(
            f"Unknown scholarly source(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(SCHOLAR_SOURCES)}.",
            code="unknown_scholar_source",
        )
    year_from, year_to = years
    if year_from and year_to and year_from > year_to:
        raise InvalidInputError("year_from must not be after year_to.")

    outcomes = await asyncio.gather(
        *(SCHOLAR_SOURCES[source].search(http, query, limit, years, settings) for source in chosen),
        return_exceptions=True,
    )
    groups: list[list[Paper]] = []
    errors: dict[str, str] = {}
    for source, outcome in zip(chosen, outcomes, strict=True):
        if isinstance(outcome, AppError):
            errors[source] = str(outcome)
        elif isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome  # cancellation and friends must propagate
            logger.exception("Scholarly source %s failed", source, exc_info=outcome)
            errors[source] = f"{SCHOLAR_SOURCES[source].name} returned an unexpected response."
        else:
            groups.append(outcome)
    return ScholarSearchResponse(query=query, papers=merge_papers(groups, limit), errors=errors)
