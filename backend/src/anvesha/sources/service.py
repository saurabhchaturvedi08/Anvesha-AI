"""Adding sources to a project: fetch or upload, extract, de-duplicate, chunk, store, embed."""

import hashlib
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.config import Settings
from anvesha.db.models import Project, Source, SourceChunk
from anvesha.db.session import Database
from anvesha.errors import InvalidInputError, NotFoundError
from anvesha.ingest.chunking import chunk_text
from anvesha.ingest.extract import ExtractedDocument, extract_document
from anvesha.llm.errors import LLMError
from anvesha.llm.gateway import LLMGateway
from anvesha.net import UnsafeURLError, require_http_url
from anvesha.sources.retrieval import SearchOutcome, search_chunks
from anvesha.storage import FileStorage, project_prefix, source_file_key
from anvesha.verify.quotes import QuoteMatch, match_quote
from anvesha.web.fetch import PDF_TYPE, SafeFetcher, normalize_url, sniff_media_type

logger = logging.getLogger(__name__)

AddedBy = Literal["user", "agent"]
UPLOAD_TYPES = {
    ".pdf": PDF_TYPE,
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}
UPLOAD_MEDIA_TYPES = frozenset(UPLOAD_TYPES.values())


@dataclass(frozen=True)
class IngestResult:
    source: Source
    created: bool  # False when the same URL or identical text was already in the project


@dataclass(frozen=True)
class SourceMetadata:
    """Metadata known before fetching (e.g. from a scholarly search). Fills extraction gaps."""

    title: str | None = None
    authors: tuple[str, ...] = ()
    published: str | None = None
    doi: str | None = None
    venue: str | None = None


async def require_project(
    session: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id)
    )
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def get_source(
    session: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID, source_id: uuid.UUID
) -> Source:
    source = await session.scalar(
        select(Source).where(
            Source.id == source_id,
            Source.project_id == project_id,
            Source.workspace_id == workspace_id,
        )
    )
    if source is None:
        raise NotFoundError("Source not found in this project")
    return source


def upload_media_type(filename: str, declared: str | None, head: bytes) -> str:
    suffix = PurePath(filename).suffix.lower()
    media_type = UPLOAD_TYPES.get(suffix) or (declared or "").split(";")[0].strip().lower()
    media_type = sniff_media_type(media_type, head)
    if media_type not in UPLOAD_MEDIA_TYPES:
        raise InvalidInputError(
            "Unsupported file type. Upload a PDF, plain text, Markdown, or HTML file.",
            code="unsupported_content_type",
        )
    if media_type == PDF_TYPE and not head.lstrip().startswith(b"%PDF-"):
        raise InvalidInputError("The file isn't a valid PDF.", code="pdf_invalid")
    return media_type


class SourceService:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        fetcher: SafeFetcher,
        storage: FileStorage,
        gateway: LLMGateway,
    ) -> None:
        self._db = db
        self._settings = settings
        self._fetcher = fetcher
        self._storage = storage
        self._gateway = gateway

    async def add_from_url(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        url: str,
        *,
        added_by: AddedBy = "user",
        respect_robots: bool = False,
        metadata: SourceMetadata | None = None,
    ) -> IngestResult:
        try:
            require_http_url(url)
        except UnsafeURLError as exc:
            raise InvalidInputError(str(exc), code="invalid_url") from exc
        requested = normalize_url(url)
        async with self._db.session() as session:
            await require_project(session, workspace_id, project_id)
            existing = await self._find_by_url(session, project_id, requested)
            if existing is not None:
                return IngestResult(existing, created=False)

        fetched = await self._fetcher.fetch(url, respect_robots=respect_robots)
        final_url = normalize_url(fetched.url)
        if final_url != requested:
            async with self._db.session() as session:
                existing = await self._find_by_url(session, project_id, final_url)
                if existing is not None:
                    return IngestResult(existing, created=False)

        document = await extract_document(
            fetched.body,
            fetched.content_type,
            charset=fetched.charset,
            url=fetched.url,
            max_pages=self._settings.pdf_max_pages,
        )
        if document.title is None and metadata is None:
            # Last resort: the file name in the URL ("report-2025.pdf" -> "report-2025").
            name = PurePath(urlsplit(fetched.url).path).stem
            document = replace(document, title=unquote(name)[:1000] or None)
        details: dict[str, Any] = {}
        if final_url != requested:
            details["requested_url"] = url
        return await self._save(
            workspace_id=workspace_id,
            project_id=project_id,
            document=document,
            origin="web",
            added_by=added_by,
            url=final_url,
            content_type=fetched.content_type,
            body=fetched.body,
            filename=None,
            fetched_at=fetched.fetched_at,
            metadata=metadata,
            details=details,
        )

    async def add_upload(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        filename: str,
        declared_type: str | None,
        body: bytes,
        added_by: AddedBy = "user",
    ) -> IngestResult:
        if not body:
            raise InvalidInputError("The file is empty.", code="empty_file")
        media_type = upload_media_type(filename, declared_type, body[:1024])
        async with self._db.session() as session:
            await require_project(session, workspace_id, project_id)
        document = await extract_document(body, media_type, max_pages=self._settings.pdf_max_pages)
        if document.title is None:
            document = replace(document, title=PurePath(filename).stem[:1000] or None)
        return await self._save(
            workspace_id=workspace_id,
            project_id=project_id,
            document=document,
            origin="upload",
            added_by=added_by,
            url=None,
            content_type=media_type,
            body=body,
            filename=PurePath(filename).name[:255],
            fetched_at=None,
            metadata=None,
            details={},
        )

    async def _find_by_url(
        self, session: AsyncSession, project_id: uuid.UUID, url: str
    ) -> Source | None:
        return await session.scalar(
            select(Source).where(Source.project_id == project_id, Source.url == url)
        )

    async def _save(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        document: ExtractedDocument,
        origin: Literal["web", "upload"],
        added_by: AddedBy,
        url: str | None,
        content_type: str,
        body: bytes,
        filename: str | None,
        fetched_at: datetime | None,
        metadata: SourceMetadata | None,
        details: dict[str, Any],
    ) -> IngestResult:
        text_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
        async with self._db.session() as session:
            existing = await session.scalar(
                select(Source).where(Source.project_id == project_id, Source.text_hash == text_hash)
            )
            if existing is not None:
                return IngestResult(existing, created=False)

        chunks = chunk_text(
            document.text,
            max_chars=self._settings.chunk_max_chars,
            overlap=self._settings.chunk_overlap_chars,
        )
        all_details = {**document.details, **details}
        if len(chunks) > self._settings.max_chunks_per_source:
            all_details["indexed_chars"] = chunks[self._settings.max_chunks_per_source - 1].end
            chunks = chunks[: self._settings.max_chunks_per_source]
        meta = metadata or SourceMetadata()
        if meta.venue:
            all_details["venue"] = meta.venue

        source_id = uuid.uuid4()
        storage_key = None
        if content_type == PDF_TYPE:
            storage_key = source_file_key(workspace_id, project_id, source_id, "pdf")
            await self._storage.put(storage_key, body)

        source = Source(
            id=source_id,
            workspace_id=workspace_id,
            project_id=project_id,
            source_type=document.source_type,
            origin=origin,
            added_by=added_by,
            url=url,
            title=document.title or meta.title,
            authors=document.authors or list(meta.authors),
            published=meta.published or document.published,
            site_name=document.site_name,
            doi=meta.doi,
            content_type=content_type,
            filename=filename,
            storage_key=storage_key,
            byte_size=len(body),
            text=document.text,
            text_hash=text_hash,
            char_count=len(document.text),
            details=all_details,
            embedding_status="pending",
            fetched_at=fetched_at,
        )
        try:
            async with self._db.session() as session:
                session.add(source)
                await session.flush()
                session.add_all(
                    SourceChunk(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        source_id=source_id,
                        ordinal=chunk.ordinal,
                        char_start=chunk.start,
                        char_end=chunk.end,
                        text=chunk.text,
                    )
                    for chunk in chunks
                )
                await session.commit()
        except IntegrityError:
            # Someone added the same URL or text concurrently; return theirs.
            if storage_key:
                await self._storage.delete(storage_key)
            async with self._db.session() as session:
                existing = await session.scalar(
                    select(Source).where(
                        Source.project_id == project_id,
                        (Source.text_hash == text_hash) | (Source.url == url),
                    )
                )
            if existing is None:
                raise
            return IngestResult(existing, created=False)
        except BaseException:
            if storage_key:
                await self._storage.delete(storage_key)
            raise

        await self.embed_source(workspace_id, project_id, source_id)
        async with self._db.session() as session:
            refreshed = await session.get(Source, source_id)
        return IngestResult(refreshed or source, created=True)

    async def embed_source(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, source_id: uuid.UUID
    ) -> str:
        """(Re-)embed a source's chunks with the workspace's embedding model.

        Never raises for model problems: the source stays keyword-searchable, and the
        outcome is recorded in ``embedding_status`` / ``embedding_error``.
        """
        route = await self._gateway.embedding_route(workspace_id)
        if route is None:
            await self._set_embedding_status(source_id, "skipped", None, None)
            return "skipped"
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(SourceChunk.id, SourceChunk.text)
                    .where(SourceChunk.source_id == source_id)
                    .order_by(SourceChunk.ordinal)
                )
            ).all()
        if not rows:
            await self._set_embedding_status(source_id, "skipped", None, None)
            return "skipped"
        try:
            embeddings = await self._gateway.embed(
                workspace_id=workspace_id,
                texts=[row.text for row in rows],
                project_id=project_id,
            )
        except LLMError as exc:
            logger.warning("Embedding source %s failed: %s", source_id, exc)
            await self._set_embedding_status(source_id, "failed", None, str(exc)[:2000])
            return "failed"
        async with self._db.session() as session:
            await session.execute(
                update(SourceChunk),
                [
                    {"id": row.id, "embedding": vector, "embedding_model": embeddings.model}
                    for row, vector in zip(rows, embeddings.vectors, strict=True)
                ],
            )
            await session.commit()
        await self._set_embedding_status(source_id, "ready", embeddings.model, None)
        return "ready"

    async def _set_embedding_status(
        self, source_id: uuid.UUID, status: str, model: str | None, error: str | None
    ) -> None:
        async with self._db.session() as session:
            await session.execute(
                update(Source)
                .where(Source.id == source_id)
                .values(embedding_status=status, embedding_model=model, embedding_error=error)
            )
            await session.commit()

    async def search(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        query: str,
        *,
        limit: int = 8,
        source_ids: list[uuid.UUID] | None = None,
    ) -> SearchOutcome:
        """Hybrid search when an embedding model is set up, keyword search otherwise."""
        query_vector = None
        embedding_model = None
        note = None
        route = await self._gateway.embedding_route(workspace_id)
        if route is None:
            note = "No embedding model is configured, so only keyword search was used."
        else:
            try:
                embeddings = await self._gateway.embed(
                    workspace_id=workspace_id, texts=[query], project_id=project_id
                )
                query_vector, embedding_model = embeddings.vectors[0], embeddings.model
            except LLMError as exc:
                note = f"Semantic search unavailable ({exc.code}); used keyword search only."
        async with self._db.session() as session:
            await require_project(session, workspace_id, project_id)
            return await search_chunks(
                session,
                workspace_id=workspace_id,
                project_id=project_id,
                query=query,
                limit=limit,
                query_vector=query_vector,
                embedding_model=embedding_model,
                source_ids=source_ids,
                semantic_note=note,
            )

    async def verify_quote(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, source_id: uuid.UUID, quote: str
    ) -> tuple[Source, QuoteMatch]:
        async with self._db.session() as session:
            source = await get_source(session, workspace_id, project_id, source_id)
        return source, match_quote(quote, source.text)

    async def delete(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, source_id: uuid.UUID
    ) -> None:
        async with self._db.session() as session:
            source = await get_source(session, workspace_id, project_id, source_id)
            storage_key = source.storage_key
            await session.delete(source)
            await session.commit()
        if storage_key:
            await self._delete_file(storage_key)

    async def delete_project_files(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> None:
        await self._delete_file(project_prefix(workspace_id, project_id), prefix=True)

    async def _delete_file(self, key: str, *, prefix: bool = False) -> None:
        try:
            if prefix:
                await self._storage.delete_prefix(key)
            else:
                await self._storage.delete(key)
        except OSError:
            # The database row is gone either way; an orphaned file is harmless.
            logger.exception("Failed to delete stored file %s", key)


def read_text(source: Source, offset: int, max_chars: int) -> tuple[str, int | None]:
    """A window of a source's text, and the offset to continue from (None at the end)."""
    offset = max(0, min(offset, source.char_count))
    end = min(source.char_count, offset + max_chars)
    if end < source.char_count:
        # Don't cut a word in half.
        space = source.text.rfind(" ", offset + max_chars // 2, end)
        if space != -1:
            end = space + 1
    return source.text[offset:end], end if end < source.char_count else None
