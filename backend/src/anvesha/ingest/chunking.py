"""Split source text into overlapping passages that keep exact offsets into the original."""

from dataclasses import dataclass

# Preferred break points, best first: paragraph, line, sentence, word.
_SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", " ")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    start: int
    end: int
    text: str  # always source_text[start:end]


def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 200) -> list[Chunk]:
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    overlap = max(0, min(overlap, max_chars // 2))
    chunks: list[Chunk] = []
    position = 0
    length = len(text)
    while position < length:
        end = min(position + max_chars, length)
        if end < length:
            end = _break_point(text, position + max_chars // 2, end)
        start, stop = _trim(text, position, end)
        if stop > start:
            chunks.append(Chunk(len(chunks), start, stop, text[start:stop]))
        if end >= length:
            break
        next_position = max(end - overlap, position + 1)
        # Start the next chunk on a word boundary.
        boundary = text.find(" ", next_position, end)
        position = boundary + 1 if boundary != -1 else next_position
    return chunks


def _break_point(text: str, earliest: int, latest: int) -> int:
    for separator in _SEPARATORS:
        index = text.rfind(separator, earliest, latest)
        if index != -1:
            return index + len(separator)
    return latest


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
