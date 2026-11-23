from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from anvesha.config import Settings
from anvesha.db.models import ModelPreference, SourceChunk, UsageEvent
from anvesha.llm.errors import LLMRateLimitError
from tests.conftest import TEST_STORAGE_DIR, FakeAdapter, FakeWeb, make_pdf, make_settings

ARTICLE_URL = "https://news.example/solar"
ARTICLE = """<html><head><title>Solar boom</title></head><body><article>
<h1>Solar boom</h1>
<p>Global solar capacity grew by 24 percent in 2025, according to the agency\u2019s annual report.</p>
<p>Growth was strongest in India, where new installations doubled compared with the previous year.</p>
<p>Wind power, by contrast, grew by only 6 percent as supply chains remained tight.</p>
</article></body></html>"""


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return make_settings(
        database_url=database_url,
        openai_api_key="sk-deployment",
        upload_max_bytes=50_000,
        chunk_max_chars=200,
        chunk_overlap_chars=40,
    )


async def _project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects", json={"title": "Energy", "objective": "How fast is solar growing?"}
    )
    return str(response.json()["id"])


async def _enable_embeddings(app: FastAPI) -> None:
    async with app.state.db.session() as session:
        session.add(
            ModelPreference(
                workspace_id=app.state.local_principal.workspace_id,
                role="embedding",
                provider="openai",
                model="text-embedding-3-small",
            )
        )
        await session.commit()


async def _add_article(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/projects/{project_id}/sources", json={"url": ARTICLE_URL}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_adding_a_web_page_saves_text_and_chunks(
    app: FastAPI, client: AsyncClient, fake_web: FakeWeb, fake_llm: FakeAdapter
) -> None:
    fake_web.page(ARTICLE_URL, ARTICLE)
    project_id = await _project(client)
    source = await _add_article(client, project_id)

    assert source["created"] is True
    assert (source["title"], source["source_type"], source["origin"]) == (
        "Solar boom",
        "html",
        "web",
    )
    assert source["url"] == ARTICLE_URL
    # No embedding model chosen: keyword search only, and nothing was sent to a model.
    assert source["embedding_status"] == "skipped" and fake_llm.embed_calls == []

    text = await client.get(f"/api/v1/projects/{project_id}/sources/{source['id']}/text")
    assert "grew by 24 percent in 2025" in text.json()["text"]
    async with app.state.db.session() as session:
        chunks = (await session.scalars(select(SourceChunk).order_by(SourceChunk.ordinal))).all()
    full_text = text.json()["text"]
    assert len(chunks) > 1
    assert all(full_text[c.char_start : c.char_end] == c.text for c in chunks)


async def test_same_url_or_same_text_is_not_saved_twice(
    client: AsyncClient, fake_web: FakeWeb, fake_llm: FakeAdapter
) -> None:
    fake_web.page(ARTICLE_URL, ARTICLE)
    fake_web.redirect("https://short.example/s", ARTICLE_URL)
    fake_web.page("https://mirror.example/copy", ARTICLE)
    project_id = await _project(client)
    first = await _add_article(client, project_id)

    for url in (ARTICLE_URL + "#section", "https://short.example/s", "https://mirror.example/copy"):
        again = await client.post(f"/api/v1/projects/{project_id}/sources", json={"url": url})
        assert again.status_code == 200, url
        assert again.json()["id"] == first["id"] and again.json()["created"] is False
    # The exact same URL is recognised without fetching it again.
    assert fake_web.requests.count(ARTICLE_URL) == 2  # first add, and via the redirect

    listed = await client.get(f"/api/v1/projects/{project_id}/sources")
    assert len(listed.json()) == 1


async def test_fetch_problems_are_reported_clearly(
    client: AsyncClient, fake_web: FakeWeb, fake_llm: FakeAdapter
) -> None:
    project_id = await _project(client)
    fake_web.page("https://img.example/cat.png", b"\x89PNG", "image/png")
    cases = {
        "ftp://files.example/x": (422, "invalid_url"),
        "https://img.example/cat.png": (422, "unsupported_content_type"),
        "https://down.example/": (502, "fetch_failed"),
    }
    for url, (status_code, code) in cases.items():
        response = await client.post(f"/api/v1/projects/{project_id}/sources", json={"url": url})
        assert (response.status_code, response.json()["code"]) == (status_code, code), url


async def test_uploading_a_pdf_stores_the_file_and_page_offsets(
    app: FastAPI, client: AsyncClient, fake_llm: FakeAdapter
) -> None:
    project_id = await _project(client)
    pdf = make_pdf(["Battery storage costs fell 40 percent.", "Grid operators adopted it widely."])
    response = await client.post(
        f"/api/v1/projects/{project_id}/sources/upload",
        files={"file": ("storage-report.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    source = response.json()
    assert (source["source_type"], source["origin"], source["filename"]) == (
        "pdf",
        "upload",
        "storage-report.pdf",
    )
    # No title metadata, and the first line is a sentence rather than a title: file name.
    assert source["title"] == "storage-report"
    assert "title_guessed" not in source["details"]
    assert source["details"]["page_count"] == 2

    stored = list(TEST_STORAGE_DIR.rglob("original.pdf"))
    assert any(path.read_bytes() == pdf for path in stored)

    verified = await client.post(
        f"/api/v1/projects/{project_id}/verify-quote",
        json={"source_id": source["id"], "quote": "Grid operators adopted it widely"},
    )
    assert verified.json()["spans"][0]["page"] == 2

    # Deleting the source removes its stored file too.
    stored_path = next(path for path in stored if path.read_bytes() == pdf)
    deleted = await client.delete(f"/api/v1/projects/{project_id}/sources/{source['id']}")
    assert deleted.status_code == 204 and not stored_path.exists()


async def test_upload_validation(client: AsyncClient, fake_llm: FakeAdapter) -> None:
    project_id = await _project(client)
    url = f"/api/v1/projects/{project_id}/sources/upload"
    cases = [
        (("photo.png", b"\x89PNG", "image/png"), "unsupported_content_type"),
        (("fake.pdf", b"not a pdf at all", "application/pdf"), "pdf_invalid"),
        (("empty.txt", b"", "text/plain"), "empty_file"),
        (("huge.txt", b"x" * 60_000, "text/plain"), "file_too_large"),
    ]
    for file, code in cases:
        response = await client.post(url, files={"file": file})
        assert (response.status_code, response.json()["code"]) == (422, code), file[0]

    notes = await client.post(
        url, files={"file": ("notes.md", b"# My notes\n\nSolar is cheap.", "")}
    )
    assert notes.status_code == 201 and notes.json()["title"] == "My notes"


async def test_embeddings_power_hybrid_search(
    app: FastAPI, client: AsyncClient, fake_web: FakeWeb, fake_llm: FakeAdapter
) -> None:
    await _enable_embeddings(app)
    fake_web.page(ARTICLE_URL, ARTICLE)
    project_id = await _project(client)
    source = await _add_article(client, project_id)
    assert (source["embedding_status"], source["embedding_model"]) == (
        "ready",
        "text-embedding-3-small",
    )

    response = await client.post(
        f"/api/v1/projects/{project_id}/sources/search",
        json={"query": "How much did wind power grow?"},
    )
    body = response.json()
    assert body["mode"] == "hybrid" and body["semantic_note"] is None
    top = body["hits"][0]
    assert "Wind power" in top["text"]
    assert set(top["matched_by"]) == {"keyword", "semantic"}

    async with app.state.db.session() as session:
        roles = (await session.scalars(select(UsageEvent.role))).all()
    assert set(roles) == {"embedding"}  # chunks at ingest, plus the query


async def test_keyword_search_without_embeddings_and_failed_embeddings(
    app: FastAPI, client: AsyncClient, fake_web: FakeWeb, fake_llm: FakeAdapter
) -> None:
    fake_web.page(ARTICLE_URL, ARTICLE)
    project_id = await _project(client)
    source = await _add_article(client, project_id)
    search_url = f"/api/v1/projects/{project_id}/sources/search"

    keyword = (await client.post(search_url, json={"query": "India installations"})).json()
    assert keyword["mode"] == "keyword" and "No embedding model" in keyword["semantic_note"]
    assert "India" in keyword["hits"][0]["text"]
    assert (await client.post(search_url, json={"query": "zeppelins"})).json()["hits"] == []

    # Embedding fails (rate limit): the source stays searchable and can be retried later.
    await _enable_embeddings(app)
    fake_llm.embed_error = LLMRateLimitError("slow down")
    retry_url = f"/api/v1/projects/{project_id}/sources/{source['id']}/embed"
    failed = (await client.post(retry_url)).json()
    assert failed["embedding_status"] == "failed" and "slow down" in failed["embedding_error"]
    degraded = (await client.post(search_url, json={"query": "India installations"})).json()
    assert degraded["mode"] == "keyword" and "provider_rate_limited" in degraded["semantic_note"]

    fake_llm.embed_error = None
    assert (await client.post(retry_url)).json()["embedding_status"] == "ready"
    async with app.state.db.session() as session:
        missing = await session.scalar(
            select(func.count()).select_from(SourceChunk).where(SourceChunk.embedding.is_(None))
        )
    assert missing == 0


async def test_verify_quote_endpoint(
    client: AsyncClient, fake_web: FakeWeb, fake_llm: FakeAdapter
) -> None:
    fake_web.page(ARTICLE_URL, ARTICLE)
    project_id = await _project(client)
    source = await _add_article(client, project_id)
    url = f"/api/v1/projects/{project_id}/verify-quote"

    good = (
        await client.post(
            url,
            json={
                "source_id": source["id"],
                "quote": "solar capacity grew by 24 percent in 2025, according to the agency's annual report",
            },
        )
    ).json()
    assert good["supported"] is True and good["status"] == "normalized"
    assert good["spans"][0]["text"].startswith("solar capacity grew")
    assert "Global" in good["context"]

    bad = (
        await client.post(
            url,
            json={"source_id": source["id"], "quote": "solar capacity grew by 30 percent in 2025"},
        )
    ).json()
    assert (bad["supported"], bad["status"], bad["spans"]) == (False, "not_found", [])


async def test_sources_are_isolated_per_project_and_cleaned_up_with_it(
    app: FastAPI, client: AsyncClient, fake_llm: FakeAdapter
) -> None:
    project_a, project_b = await _project(client), await _project(client)
    pdf = make_pdf(["Only in project A."])
    created = await client.post(
        f"/api/v1/projects/{project_a}/sources/upload",
        files={"file": ("a.pdf", pdf, "application/pdf")},
    )
    source_id = created.json()["id"]

    for path in (f"/sources/{source_id}", f"/sources/{source_id}/text"):
        assert (await client.get(f"/api/v1/projects/{project_b}{path}")).status_code == 404
    other = await client.post(
        f"/api/v1/projects/{project_b}/verify-quote",
        json={"source_id": source_id, "quote": "Only in project A here"},
    )
    assert other.status_code == 404
    unknown = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/projects/{unknown}/sources")).status_code == 404

    files_before = {p for p in Path(TEST_STORAGE_DIR).rglob("*") if p.is_file()}
    project_dir = next(p for p in files_before if source_id in str(p)).parents[2]
    assert (await client.delete(f"/api/v1/projects/{project_a}")).status_code == 204
    assert not project_dir.exists()
