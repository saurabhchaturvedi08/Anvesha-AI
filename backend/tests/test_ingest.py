from itertools import pairwise
from pathlib import Path

import pytest

from anvesha.ingest.chunking import chunk_text
from anvesha.ingest.extract import (
    ExtractionError,
    clean_text,
    decode_text,
    extract_document,
    extract_html,
    extract_pdf,
    guess_title,
    page_for_offset,
)
from anvesha.storage import LocalFileStorage, source_file_key
from tests.conftest import make_pdf

ARTICLE = """<html><head><title>Solar Report 2025</title>
<meta name="author" content="Asha Rao; Ben Ode"></head>
<body><nav>Home | About | Login</nav>
<article><h1>Solar Report 2025</h1>
<p>Global solar capacity grew by 24 percent in 2025, the agency\u2019s analysts said.</p>
<p>Growth was strongest in India, where installations doubled compared with 2024.</p>
<p>Analysts expect a further rise next year as panel prices keep falling.</p>
</article><footer>Cookie settings</footer></body></html>"""


def test_chunks_cover_the_text_with_exact_offsets() -> None:
    text = " ".join(f"Sentence number {i} talks about solar energy." for i in range(300))
    chunks = chunk_text(text, max_chars=400, overlap=80)
    assert len(chunks) > 10
    for index, chunk in enumerate(chunks):
        assert chunk.ordinal == index
        assert chunk.text == text[chunk.start : chunk.end]
        assert len(chunk.text) <= 400
        assert not chunk.text[0].isspace() and not chunk.text[-1].isspace()
    # Consecutive chunks overlap, and together they reach the end of the text.
    assert all(b.start < a.end for a, b in pairwise(chunks))
    assert chunks[0].start == 0 and chunks[-1].end == len(text)


def test_chunks_prefer_paragraph_breaks() -> None:
    text = ("Alpha " * 60).strip() + "\n\n" + ("Beta " * 60).strip()
    chunks = chunk_text(text, max_chars=500, overlap=0)
    assert chunks[0].text.endswith("Alpha") and chunks[1].text.startswith("Beta")


def test_short_and_empty_text() -> None:
    assert chunk_text("") == []
    assert [c.text for c in chunk_text("  just one line  ")] == ["just one line"]


def test_decoding_honours_charset_meta_and_detection() -> None:
    text = "café “quoted”"
    assert decode_text(text.encode("utf-8"), None) == text
    assert decode_text("café".encode("latin-1"), "iso-8859-1") == "café"
    html = b'<meta charset="windows-1252"><p>caf\xe9 \x93q\x94</p>'
    assert "café “q”" in decode_text(html, None, html=True)
    assert decode_text(b"\xef\xbb\xbfhello", None) == "hello"


def test_clean_text_normalises_whitespace_but_keeps_words() -> None:
    assert clean_text("a\r\nb\t c  \n\n\n\nd\x00") == "a\nb c\n\nd"


def test_html_extraction_keeps_the_article_and_metadata() -> None:
    document = extract_html(ARTICLE.encode("utf-8"), "utf-8", "https://example.org/solar")
    assert document.source_type == "html"
    assert document.title == "Solar Report 2025"
    assert document.authors == ["Asha Rao", "Ben Ode"]
    assert "the agency\u2019s analysts said" in document.text
    assert "Cookie settings" not in document.text


def test_html_without_text_is_rejected() -> None:
    with pytest.raises(ExtractionError, match="No readable text"):
        extract_html(b"<html><body><script>x()</script></body></html>", None, None)


def test_pdf_extraction_records_pages() -> None:
    document = extract_pdf(make_pdf(["First page about solar.", "Second page about wind."]), 100)
    assert document.source_type == "pdf"
    assert document.text == "First page about solar.\n\nSecond page about wind."
    assert document.details["page_count"] == 2
    assert page_for_offset(document.details, document.text.index("wind")) == 2
    assert page_for_offset(document.details, 0) == 1
    assert page_for_offset({}, 10) is None


def test_pdf_page_limit_and_invalid_pdfs() -> None:
    document = extract_pdf(make_pdf(["one", "two", "three"]), 2)
    assert document.details["truncated_at_page"] == 2 and "three" not in document.text
    with pytest.raises(ExtractionError) as excinfo:
        extract_pdf(b"%PDF-1.4 not really", 10)
    assert excinfo.value.code == "pdf_invalid"
    with pytest.raises(ExtractionError, match="no extractable text"):
        extract_pdf(make_pdf([""]), 10)


async def test_extract_document_dispatches_by_type() -> None:
    plain = await extract_document(b"# Notes\n\nSome text here.", "text/markdown")
    assert (plain.source_type, plain.title) == ("text", "Notes")
    with pytest.raises(ExtractionError):
        await extract_document(b"\x00", "image/png")


async def test_local_storage_round_trip_and_key_safety(tmp_path: Path) -> None:
    import uuid

    storage = LocalFileStorage(tmp_path)
    key = source_file_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "pdf")
    await storage.put(key, b"%PDF-data")
    assert await storage.get(key) == b"%PDF-data"
    await storage.delete(key)
    await storage.delete(key)  # deleting twice is fine
    for bad in ("../escape", "a/../../b", "/abs/path", "a b"):
        with pytest.raises(ValueError):
            await storage.put(bad, b"x")
    await storage.put("workspaces/w/projects/p/sources/s/original.pdf", b"x")
    await storage.delete_prefix("workspaces/w/projects/p")
    assert not (tmp_path / "workspaces" / "w" / "projects" / "p").exists()


@pytest.mark.parametrize(
    ("text", "title"),
    [
        (
            "Retrieval-Augmented Generation for\nKnowledge-Intensive NLP Tasks\nPatrick Lewis",
            "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        ),
        ("Attention Is All You Need\nAshish Vaswani", "Attention Is All You Need"),
        (
            "Provided proper attribution is provided, Google hereby grants permission to\n"
            "reproduce the tables and figures in this paper solely for use in journalistic or\n"
            "scholarly works.\nAttention Is All You Need\nAshish Vaswani",
            "Attention Is All You Need",
        ),
        (
            "arXiv:2005.11401v4 [cs.CL] 12 Apr 2021\nDense Passage Retrieval",
            "Dense Passage Retrieval",
        ),
        ("12\nshort", None),  # page numbers and fragments aren't titles
        ("", None),
    ],
)
def test_title_guess_from_first_lines(text: str, title: str | None) -> None:
    assert guess_title(text) == title
