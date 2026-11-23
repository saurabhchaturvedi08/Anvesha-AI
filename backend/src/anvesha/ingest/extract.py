"""Turn fetched or uploaded bytes into clean text plus metadata.

Extraction is CPU-bound, so callers run it in a worker thread (see ``extract_document``).
"""

import asyncio
import codecs
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

import charset_normalizer
import pypdfium2
import trafilatura

from anvesha.errors import AppError

SourceType = Literal["html", "pdf", "text"]

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.I)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_SPACE_RUNS = re.compile(r" {2,}")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")
_PDF_DATE = re.compile(r"^D?:?(\d{4})(\d{2})?(\d{2})?")
# PDFium is not thread-safe, so PDF extraction runs one document at a time per process.
_PDFIUM_LOCK = threading.Lock()


class ExtractionError(AppError):
    code = "extraction_failed"
    http_status = 422


@dataclass(frozen=True)
class ExtractedDocument:
    source_type: SourceType
    text: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    published: str | None = None
    site_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def clean_text(text: str) -> str:
    """Normalise line endings and whitespace without changing the words themselves."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _CONTROL_CHARS.sub("", text).replace("\t", " ")
    text = _SPACE_RUNS.sub(" ", _TRAILING_SPACE.sub("\n", text))
    return _MANY_BLANK_LINES.sub("\n\n", text).strip()


def decode_text(body: bytes, charset: str | None, *, html: bool = False) -> str:
    """Decode with the declared charset, then a BOM or <meta charset>, then detection."""
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if body.startswith(bom):
            candidates.insert(0, encoding)
    if html and (match := _META_CHARSET.search(body[:4096])):
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    candidates.append("utf-8")
    for encoding in candidates:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    best = charset_normalizer.from_bytes(body).best()
    return str(best) if best is not None else body.decode("latin-1")


def extract_html(body: bytes, charset: str | None, url: str | None) -> ExtractedDocument:
    html = decode_text(body, charset, html=True)
    document = trafilatura.bare_extraction(
        html,
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    text = ""
    title = authors = published = site_name = None
    if document is not None and not isinstance(document, dict):
        text = document.text or ""
        title, authors = document.title, document.author
        published, site_name = document.date, document.sitename
    if not text.strip():
        # Pages without a clear main-content block: fall back to all visible text.
        text = trafilatura.html2txt(html) or ""
    text = clean_text(text)
    if not text:
        raise ExtractionError("No readable text was found on the page.", code="no_text")
    return ExtractedDocument(
        source_type="html",
        text=text,
        title=_clip(title, 1000),
        authors=_split_authors(authors),
        published=_clip(published, 32),
        site_name=_clip(site_name, 255),
    )


def extract_pdf(body: bytes, max_pages: int) -> ExtractedDocument:
    with _PDFIUM_LOCK:
        try:
            pdf = pypdfium2.PdfDocument(body)
        except pypdfium2.PdfiumError as exc:
            message = str(exc).lower()
            if "password" in message:
                raise ExtractionError(
                    "The PDF is password-protected.", code="pdf_encrypted"
                ) from exc
            raise ExtractionError("The file isn't a readable PDF.", code="pdf_invalid") from exc
        try:
            page_count = len(pdf)
            metadata = pdf.get_metadata_dict(skip_empty=True)
            pages: list[str] = []
            for index in range(min(page_count, max_pages)):
                page = pdf[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        pages.append(clean_text(text_page.get_text_range()))
                    finally:
                        text_page.close()
                finally:
                    page.close()
        finally:
            pdf.close()

    # Join pages and remember where each starts, so citations can name the page.
    parts: list[str] = []
    page_offsets: list[int] = []
    position = 0
    for page_text in pages:
        if parts:
            parts.append("\n\n")
            position += 2
        page_offsets.append(position)
        parts.append(page_text)
        position += len(page_text)
    text = "".join(parts)
    if not text.strip():
        raise ExtractionError(
            "The PDF has no extractable text (it may be a scanned image; OCR isn't supported yet).",
            code="no_text",
        )
    details: dict[str, Any] = {"page_count": page_count, "page_offsets": page_offsets}
    if page_count > max_pages:
        details["truncated_at_page"] = max_pages
    title = _clip(metadata.get("Title"), 1000)
    if title is None and (title := guess_title(text)) is not None:
        details["title_guessed"] = True
    return ExtractedDocument(
        source_type="pdf",
        text=text,
        title=title,
        authors=_split_authors(metadata.get("Author")),
        published=_pdf_date(metadata.get("CreationDate")),
        details=details,
    )


def extract_plain(body: bytes, charset: str | None) -> ExtractedDocument:
    text = clean_text(decode_text(body, charset))
    if not text:
        raise ExtractionError("The file is empty.", code="no_text")
    first_line = text.split("\n", 1)[0].lstrip("# ").strip()
    return ExtractedDocument(source_type="text", text=text, title=_clip(first_line, 200))


async def extract_document(
    body: bytes,
    content_type: str,
    *,
    charset: str | None = None,
    url: str | None = None,
    max_pages: int = 1000,
) -> ExtractedDocument:
    if content_type == "application/pdf":
        return await asyncio.to_thread(extract_pdf, body, max_pages)
    if content_type in ("text/html", "application/xhtml+xml"):
        return await asyncio.to_thread(extract_html, body, charset, url)
    if content_type.startswith("text/"):
        return await asyncio.to_thread(extract_plain, body, charset)
    raise ExtractionError(
        f"Unsupported content type {content_type!r}.", code="unsupported_content_type"
    )


def page_for_offset(details: dict[str, Any], offset: int) -> int | None:
    """1-based PDF page containing a character offset, if page offsets are known."""
    offsets = details.get("page_offsets")
    if not isinstance(offsets, list) or not offsets:
        return None
    page = 0
    for index, start in enumerate(offsets):
        if isinstance(start, int) and start <= offset:
            page = index
        else:
            break
    return page + 1


# Words that, ending a title's first line, mean the title continues on the next one.
_TITLE_CONTINUES = frozenset(
    {"a", "an", "and", "by", "for", "from", "in", "of", "on", "the", "to"}
    | {"toward", "towards", "using", "via", "vs", "with"}
)


_NOT_A_TITLE = re.compile(
    r"copyright|©|permission|licen[cs]e|all rights|arxiv:|doi\b|https?://|www\.|@|"
    r"preprint|proceedings|accepted|published|journal of|\bvol\.|issn",
    re.IGNORECASE,
)


def _looks_like_title(line: str) -> bool:
    words = line.split()
    return (
        2 <= len(words) <= 20
        and 8 <= len(line) <= 200
        and not line[0].islower()
        and not line.endswith((".", ",", ";"))
        and not _NOT_A_TITLE.search(line)
    )


def guess_title(text: str) -> str | None:
    """A best guess at a document's title from its first lines (papers often lack metadata).

    Skips notices and running headers ("Provided proper attribution...", "arXiv:..."), and
    joins a second line when the first clearly continues ("... Generation for").
    """
    lines = [line.strip() for line in text.split("\n", 12)[:12] if line.strip()]
    for index, line in enumerate(lines):
        if not _looks_like_title(line):
            continue
        last_word = line.rsplit(None, 1)[-1].lower()
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if (
            last_word in _TITLE_CONTINUES
            and following
            and not _NOT_A_TITLE.search(following)
            and len(line) + len(following) < 200
        ):
            return f"{line} {following}"
        return line
    return None


def _clip(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def _split_authors(value: str | None) -> list[str]:
    if not value:
        return []
    names = re.split(r";|\s+and\s+|\s*&\s*", value)
    if len(names) == 1 and value.count(",") >= 2:
        names = value.split(",")
    return [name for name in (" ".join(n.split()) for n in names) if name][:50]


def _pdf_date(value: str | None) -> str | None:
    if not value:
        return None
    match = _PDF_DATE.match(value.strip())
    if not match:
        return None
    return "-".join(part for part in match.groups() if part)
