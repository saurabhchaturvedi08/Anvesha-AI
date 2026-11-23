import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from sqlalchemy import select

from anvesha.api.deps import PrincipalDep, SessionDep, SettingsDep, SourceServiceDep
from anvesha.api.schemas.sources import (
    ChunkHitRead,
    EmbedResponse,
    QuoteSpan,
    QuoteVerifyRequest,
    QuoteVerifyResponse,
    SourceAddFromURL,
    SourceCreated,
    SourceRead,
    SourceSearchRequest,
    SourceSearchResponse,
    SourceText,
)
from anvesha.db.models import Source
from anvesha.errors import InvalidInputError
from anvesha.ingest.extract import page_for_offset
from anvesha.sources.service import IngestResult, get_source, read_text, require_project
from anvesha.verify.quotes import context_around

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["sources"])

UPLOAD_READ_CHUNK = 1024 * 1024


def _created(result: IngestResult, response: Response) -> SourceCreated:
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return SourceCreated(
        **SourceRead.model_validate(result.source).model_dump(), created=result.created
    )


@router.get("/sources", response_model=list[SourceRead])
async def list_sources(
    project_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Source]:
    await require_project(session, principal.workspace_id, project_id)
    result = await session.scalars(
        select(Source)
        .where(Source.workspace_id == principal.workspace_id, Source.project_id == project_id)
        .order_by(Source.created_at.desc(), Source.id)
        .limit(limit)
        .offset(offset)
    )
    return result.all()


@router.post(
    "/sources",
    response_model=SourceCreated,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": SourceCreated, "description": "Already saved"}},
)
async def add_source_from_url(
    project_id: uuid.UUID,
    payload: SourceAddFromURL,
    response: Response,
    principal: PrincipalDep,
    sources: SourceServiceDep,
) -> SourceCreated:
    """Fetch a web page or PDF and save its text. Returns 200 if it was already saved."""
    # A person explicitly asked for this page, so robots.txt (meant for crawlers) isn't
    # applied here. Agent fetches honour it.
    result = await sources.add_from_url(
        principal.workspace_id, project_id, payload.url, added_by="user", respect_robots=False
    )
    return _created(result, response)


@router.post(
    "/sources/upload",
    response_model=SourceCreated,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": SourceCreated, "description": "Already saved"}},
)
async def upload_source(
    project_id: uuid.UUID,
    response: Response,
    principal: PrincipalDep,
    settings: SettingsDep,
    sources: SourceServiceDep,
    file: Annotated[UploadFile, File(description="PDF, plain text, Markdown, or HTML")],
) -> SourceCreated:
    body = bytearray()
    while chunk := await file.read(UPLOAD_READ_CHUNK):
        body.extend(chunk)
        if len(body) > settings.upload_max_bytes:
            raise InvalidInputError(
                f"The file is larger than the {settings.upload_max_bytes // (1024 * 1024)} MB "
                "limit.",
                code="file_too_large",
            )
    result = await sources.add_upload(
        principal.workspace_id,
        project_id,
        filename=file.filename or "upload",
        declared_type=file.content_type,
        body=bytes(body),
    )
    return _created(result, response)


@router.get("/sources/{source_id}", response_model=SourceRead)
async def get_source_metadata(
    project_id: uuid.UUID, source_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Source:
    return await get_source(session, principal.workspace_id, project_id, source_id)


@router.get("/sources/{source_id}/text", response_model=SourceText)
async def get_source_text(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200_000)] = 20_000,
) -> SourceText:
    source = await get_source(session, principal.workspace_id, project_id, source_id)
    text, next_offset = read_text(source, offset, limit)
    return SourceText(
        source_id=source.id,
        offset=min(offset, source.char_count),
        next_offset=next_offset,
        total_chars=source.char_count,
        text=text,
    )


@router.post("/sources/{source_id}/embed", response_model=EmbedResponse)
async def embed_source(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    sources: SourceServiceDep,
) -> EmbedResponse:
    """Re-run embedding, e.g. after it failed or after choosing a new embedding model."""
    await get_source(session, principal.workspace_id, project_id, source_id)
    await sources.embed_source(principal.workspace_id, project_id, source_id)
    session.expire_all()
    source = await get_source(session, principal.workspace_id, project_id, source_id)
    return EmbedResponse(
        source_id=source.id,
        embedding_status=source.embedding_status,
        embedding_model=source.embedding_model,
        embedding_error=source.embedding_error,
    )


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    project_id: uuid.UUID, source_id: uuid.UUID, principal: PrincipalDep, sources: SourceServiceDep
) -> Response:
    await sources.delete(principal.workspace_id, project_id, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sources/search", response_model=SourceSearchResponse)
async def search_sources(
    project_id: uuid.UUID,
    payload: SourceSearchRequest,
    principal: PrincipalDep,
    sources: SourceServiceDep,
) -> SourceSearchResponse:
    outcome = await sources.search(
        principal.workspace_id,
        project_id,
        payload.query,
        limit=payload.limit,
        source_ids=payload.source_ids,
    )
    return SourceSearchResponse(
        mode=outcome.mode,
        semantic_note=outcome.semantic_note,
        hits=[ChunkHitRead.model_validate(hit, from_attributes=True) for hit in outcome.hits],
    )


@router.post("/verify-quote", response_model=QuoteVerifyResponse)
async def verify_quote(
    project_id: uuid.UUID,
    payload: QuoteVerifyRequest,
    principal: PrincipalDep,
    sources: SourceServiceDep,
) -> QuoteVerifyResponse:
    """Deterministically check that a quote appears in a source (the evidence rule)."""
    source, match = await sources.verify_quote(
        principal.workspace_id, project_id, payload.source_id, payload.quote
    )
    return QuoteVerifyResponse(
        source_id=source.id,
        status=match.status,
        supported=match.supported,
        spans=[
            QuoteSpan(
                start=start,
                end=end,
                page=page_for_offset(source.details, start),
                text=source.text[start:end],
            )
            for start, end in match.spans
        ],
        context=context_around(source.text, match.spans[0]) if match.spans else None,
    )
