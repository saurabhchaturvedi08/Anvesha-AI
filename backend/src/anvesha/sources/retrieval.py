"""Hybrid search over a project's source passages: keyword (Postgres FTS) + semantic (pgvector).

The two rankings are merged with reciprocal rank fusion, which needs no score tuning.
Without an embedding model, search is keyword-only and still works.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import ColumnElement, Float, Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.db.models import Source, SourceChunk
from anvesha.db.models.sources import FTS_CONFIG

CANDIDATES_PER_RANKING = 50
RRF_K = 60
_EXCLUSION = re.compile(r"(^|\s)-\w")

Ranking = Literal["keyword", "semantic"]


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: int
    source_id: uuid.UUID
    source_title: str | None
    source_url: str | None
    char_start: int
    char_end: int
    text: str
    score: float
    matched_by: list[Ranking] = field(default_factory=list)


@dataclass(frozen=True)
class SearchOutcome:
    hits: list[ChunkHit]
    mode: Literal["hybrid", "keyword"]
    # Why semantic search wasn't used, when it wasn't.
    semantic_note: str | None = None


def keyword_tsquery(query: str) -> ColumnElement[Any]:
    """Web-search syntax ("phrases", -exclusions), but any term may match (OR, not AND).

    Ranking still favours passages matching more terms. Queries with an exclusion keep
    AND semantics, since "a OR NOT b" would match nearly everything.
    """
    parsed: ColumnElement[Any] = func.websearch_to_tsquery(FTS_CONFIG, query)
    if _EXCLUSION.search(query):
        return parsed
    return cast(func.replace(cast(parsed, Text), " & ", " | "), TSQUERY)


async def _keyword_ranking(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    query: str,
    source_ids: Sequence[uuid.UUID] | None,
) -> list[int]:
    tsquery = keyword_tsquery(query)
    statement = (
        select(SourceChunk.id)
        .where(
            SourceChunk.workspace_id == workspace_id,
            SourceChunk.project_id == project_id,
            SourceChunk.search_vector.op("@@")(tsquery),
        )
        .order_by(func.ts_rank_cd(SourceChunk.search_vector, tsquery).desc(), SourceChunk.id)
        .limit(CANDIDATES_PER_RANKING)
    )
    if source_ids:
        statement = statement.where(SourceChunk.source_id.in_(source_ids))
    return list((await session.scalars(statement)).all())


async def _semantic_ranking(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    query_vector: list[float],
    embedding_model: str,
    source_ids: Sequence[uuid.UUID] | None,
) -> list[int]:
    distance = SourceChunk.embedding.cosine_distance(query_vector)
    statement = (
        select(SourceChunk.id)
        .where(
            SourceChunk.workspace_id == workspace_id,
            SourceChunk.project_id == project_id,
            SourceChunk.embedding_model == embedding_model,
            func.vector_dims(SourceChunk.embedding) == len(query_vector),
        )
        .order_by(cast(distance, Float), SourceChunk.id)
        .limit(CANDIDATES_PER_RANKING)
    )
    if source_ids:
        statement = statement.where(SourceChunk.source_id.in_(source_ids))
    return list((await session.scalars(statement)).all())


def reciprocal_rank_fusion(
    rankings: dict[Ranking, list[int]],
) -> list[tuple[int, float, list[Ranking]]]:
    scores: dict[int, float] = {}
    sources: dict[int, list[Ranking]] = {}
    for name, ranking in rankings.items():
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(chunk_id, []).append(name)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [(chunk_id, score, sources[chunk_id]) for chunk_id, score in ordered]


async def search_chunks(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    query: str,
    limit: int = 8,
    query_vector: list[float] | None = None,
    embedding_model: str | None = None,
    source_ids: Sequence[uuid.UUID] | None = None,
    semantic_note: str | None = None,
) -> SearchOutcome:
    rankings: dict[Ranking, list[int]] = {
        "keyword": await _keyword_ranking(session, workspace_id, project_id, query, source_ids)
    }
    if query_vector is not None and embedding_model is not None:
        rankings["semantic"] = await _semantic_ranking(
            session, workspace_id, project_id, query_vector, embedding_model, source_ids
        )
    fused = reciprocal_rank_fusion(rankings)[:limit]
    mode: Literal["hybrid", "keyword"] = "hybrid" if "semantic" in rankings else "keyword"
    if not fused:
        return SearchOutcome([], mode, semantic_note)

    rows = (
        await session.execute(
            select(SourceChunk, Source.title, Source.url)
            .join(Source, Source.id == SourceChunk.source_id)
            .where(SourceChunk.id.in_([chunk_id for chunk_id, _, _ in fused]))
        )
    ).all()
    by_id = {row[0].id: row for row in rows}
    hits = []
    for chunk_id, score, matched_by in fused:
        row = by_id.get(chunk_id)
        if row is None:
            continue
        chunk, title, url = row
        hits.append(
            ChunkHit(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                source_title=title,
                source_url=url,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                text=chunk.text,
                score=round(score, 6),
                matched_by=matched_by,
            )
        )
    return SearchOutcome(hits, mode, semantic_note)
