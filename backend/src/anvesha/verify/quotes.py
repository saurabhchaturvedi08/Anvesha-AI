"""Deterministic quote verification: does this exact passage appear in the source text?

This is the evidence check that a claim's quote must pass before it can be reported.
It forgives only differences that don't change the words: letter case, typographic
quotes and dashes, ligatures, whitespace and line breaks, and words hyphenated across a
PDF line break. It never does fuzzy matching. An ellipsis ("...", "…", "[...]") may join
fragments that appear in order and close together. An LLM may later reject a matched
quote as not supporting the claim, but nothing here can be talked into approving one.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

MIN_QUOTE_CHARS = 20
MIN_QUOTE_WORDS = 4
MIN_FRAGMENT_CHARS = 12
# Largest gap (in normalised characters) allowed between ellipsis-joined fragments.
MAX_FRAGMENT_GAP = 1500
CONTEXT_CHARS = 160

MatchStatus = Literal["exact", "normalized", "fragments", "not_found", "too_short"]

_QUOTE_CHARS = {ord(c): "'" for c in "\u2018\u2019\u201a\u201b\u2032`\u00b4"}
_QUOTE_CHARS |= {ord(c): '"' for c in "\u201c\u201d\u201e\u201f\u2033\u00ab\u00bb"}
_DASH_CHARS = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"}
_TRANSLATION = _QUOTE_CHARS | _DASH_CHARS
_INVISIBLE = frozenset("\u00ad\u200b\u200c\u200d\u2060\ufeff")
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.\s*){3}\]|\[\s*\u2026\s*\]|(?:\.\s*){3}|\u2026)\s*")
_SURROUNDING = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb \n\t"


@dataclass(frozen=True)
class QuoteMatch:
    status: MatchStatus
    # (start, end) character offsets into the original source text, one per fragment.
    spans: list[tuple[int, int]] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.status in ("exact", "normalized", "fragments")


def _normalize_with_map(text: str, *, join_hyphenated: bool) -> tuple[str, list[int]]:
    """Normalised text plus, for each output character, its offset in ``text``."""
    out: list[str] = []
    offsets: list[int] = []
    length = len(text)
    i = 0
    pending_space = False
    while i < length:
        char = text[i]
        if char in _INVISIBLE:
            i += 1
            continue
        if char.isspace():
            pending_space = bool(out)
            i += 1
            continue
        # Keep a base character together with its combining marks, so NFKC can compose.
        cluster_end = i + 1
        while cluster_end < length and unicodedata.combining(text[cluster_end]):
            cluster_end += 1
        cluster = text[i:cluster_end].translate(_TRANSLATION)

        if cluster == "-":
            after = _hyphen_line_break_end(text, i)
            if after is not None:
                if join_hyphenated and out and out[-1].isalpha() and text[after].islower():
                    i = after  # "evalu-\nation" -> "evaluation"
                    continue
                # "self-\nreported" -> "self-reported": drop the break, keep the hyphen.
                if pending_space:
                    out.append(" ")
                    offsets.append(i)
                    pending_space = False
                out.append("-")
                offsets.append(i)
                i = after
                continue

        if pending_space:
            out.append(" ")
            offsets.append(i)
            pending_space = False
        for normalized_char in unicodedata.normalize("NFKC", cluster).casefold():
            if normalized_char.isspace():
                normalized_char = " "
            out.append(normalized_char)
            offsets.append(i)
        i = cluster_end
    return "".join(out), offsets


def _hyphen_line_break_end(text: str, hyphen: int) -> int | None:
    """If a line break follows the hyphen, the index of the next non-space char."""
    j = hyphen + 1
    while j < len(text) and text[j] in " \t":
        j += 1
    if j >= len(text) or text[j] not in "\r\n":
        return None
    while j < len(text) and text[j].isspace():
        j += 1
    return j if j < len(text) else None


def normalize(text: str) -> str:
    return _normalize_with_map(text, join_hyphenated=True)[0]


def _strip_quote(quote: str) -> str:
    return quote.strip().strip(_SURROUNDING).strip()


def match_quote(quote: str, source_text: str) -> QuoteMatch:
    quote = _strip_quote(quote)
    raw_fragments = [part for part in _ELLIPSIS.split(quote) if part.strip()]
    fragments = [normalize(_strip_quote(part)) for part in raw_fragments]
    fragments = [fragment for fragment in fragments if fragment]
    joined = " ".join(fragments)
    if (
        not fragments
        or len(joined) < MIN_QUOTE_CHARS
        or len(joined.split()) < MIN_QUOTE_WORDS
        or any(len(fragment) < MIN_FRAGMENT_CHARS for fragment in fragments)
    ):
        return QuoteMatch("too_short")

    if len(raw_fragments) == 1:
        index = source_text.find(quote)
        if index != -1:
            return QuoteMatch("exact", [(index, index + len(quote))])

    status: MatchStatus = "fragments" if len(fragments) > 1 else "normalized"
    for join_hyphenated in (True, False):
        normalized_source, offsets = _normalize_with_map(
            source_text, join_hyphenated=join_hyphenated
        )
        positions = _find_in_order(normalized_source, fragments)
        if positions is not None:
            spans = [
                (offsets[start], offsets[start + len(fragment) - 1] + 1)
                for start, fragment in zip(positions, fragments, strict=True)
            ]
            return QuoteMatch(status, spans)
    return QuoteMatch("not_found")


def _find_in_order(haystack: str, fragments: list[str]) -> list[int] | None:
    """Positions of fragments appearing in order with bounded gaps, or None."""
    first = fragments[0]
    start = haystack.find(first)
    attempts = 0
    while start != -1 and attempts < 200:
        positions = [start]
        cursor = start + len(first)
        for fragment in fragments[1:]:
            found = haystack.find(fragment, cursor, cursor + MAX_FRAGMENT_GAP + len(fragment))
            if found == -1:
                break
            positions.append(found)
            cursor = found + len(fragment)
        else:
            return positions
        start = haystack.find(first, start + 1)
        attempts += 1
    return None


def context_around(source_text: str, span: tuple[int, int], width: int = CONTEXT_CHARS) -> str:
    start = max(0, span[0] - width)
    end = min(len(source_text), span[1] + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(source_text) else ""
    return f"{prefix}{source_text[start:end]}{suffix}"
