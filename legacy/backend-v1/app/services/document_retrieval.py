from __future__ import annotations

import json
import math
import os
import re
from typing import Any

import httpx
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.database import is_fts5_available
from app.models import Document, DocumentChunk

MAX_CHUNK_CHARS = 1400
CHUNK_OVERLAP_CHARS = 180


class OpenAICompatibleEmbedder:
    def __init__(self) -> None:
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.base_url = (os.getenv("ANVESHA_MODEL_BASE_URL") or f"{ollama_url}/v1").rstrip("/")
        self.model = os.getenv("ANVESHA_EMBEDDING_MODEL", "").strip()
        self.api_key = os.getenv("ANVESHA_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")

    def embed(self, inputs: list[str]) -> tuple[list[list[float]] | None, str]:
        if not self.model:
            return None, "not_configured"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model, "input": inputs},
                timeout=15.0,
            )
            response.raise_for_status()
            rows = response.json()["data"]
            rows.sort(key=lambda row: row.get("index", 0))
            vectors = [row["embedding"] for row in rows]
            if len(vectors) != len(inputs) or any(
                not isinstance(vector, list)
                or not vector
                or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)
                for vector in vectors
            ):
                return None, "unavailable: invalid_response"
            return [[float(value) for value in vector] for vector in vectors], "ready"
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            return None, "unavailable"


def split_into_chunks(content: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + MAX_CHUNK_CHARS, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + MAX_CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
    return chunks


def _query_terms(query: str) -> list[str]:
    return re.findall(r"\w+", query, flags=re.UNICODE)[:24]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def retrieve_project_chunks(db: Session, project_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    terms = _query_terms(query)
    if not terms:
        return {"results": [], "retrieval_mode": "fts5" if is_fts5_available() else "keyword"}

    embedder = OpenAICompatibleEmbedder()
    query_vectors, embedding_status = embedder.embed([query])
    if query_vectors:
        chunks = (
            db.query(DocumentChunk, Document.filename)
            .join(Document, Document.id == DocumentChunk.document_id)
            .filter(DocumentChunk.project_id == project_id, DocumentChunk.embedding_json.is_not(None))
            .all()
        )
        ranked = []
        for chunk, filename in chunks:
            try:
                vector = json.loads(chunk.embedding_json or "[]")
            except json.JSONDecodeError:
                continue
            if not isinstance(vector, list) or len(vector) != len(query_vectors[0]):
                continue
            score = _cosine_similarity(query_vectors[0], vector)
            ranked.append((score, chunk, filename))
        if ranked:
            ranked.sort(key=lambda item: item[0], reverse=True)
            return {
                "retrieval_mode": "embedding",
                "embedding_status": embedding_status,
                "results": [
                    {
                        "chunk_id": chunk.id,
                        "document_id": chunk.document_id,
                        "filename": filename,
                        "text": chunk.text,
                        "score": score,
                    }
                    for score, chunk, filename in ranked[:limit]
                ],
            }

    if is_fts5_available():
        match_query = " OR ".join(f'"{term}"' for term in terms)
        statement = text(
            "SELECT chunks.id AS chunk_id, chunks.document_id, documents.filename, chunks.text, "
            "bm25(document_chunks_fts) AS score "
            "FROM document_chunks_fts "
            "JOIN document_chunks AS chunks ON chunks.id = document_chunks_fts.rowid "
            "JOIN documents ON documents.id = chunks.document_id "
            "WHERE document_chunks_fts MATCH :match_query AND chunks.project_id = :project_id "
            "ORDER BY score LIMIT :limit"
        )
        rows = db.execute(statement, {"match_query": match_query, "project_id": project_id, "limit": limit}).mappings()
        results = [dict(row) for row in rows]
        return {"retrieval_mode": "fts5", "embedding_status": embedding_status, "results": results}

    conditions = [DocumentChunk.text.ilike(f"%{term}%") for term in terms]
    rows = (
        db.query(DocumentChunk, Document.filename)
        .join(Document, Document.id == DocumentChunk.document_id)
        .filter(DocumentChunk.project_id == project_id, or_(*conditions))
        .limit(limit)
        .all()
    )
    return {
        "retrieval_mode": "keyword",
        "embedding_status": embedding_status,
        "results": [
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "filename": filename,
                "text": chunk.text,
                "score": 0.0,
            }
            for chunk, filename in rows
        ],
    }
