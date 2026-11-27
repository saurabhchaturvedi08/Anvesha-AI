"""Scoring a research report. Pure functions, all deterministic: no model judges.

- **Citation precision**: of the findings the report cites (plus any citation markers
  the writer invented, which were dropped), the share whose quote still matches its
  source text word for word and that the verifier didn't reject.
- **Cited-sentence rate**: the share of claim sentences in the report body that carry a
  citation (sections meant for gaps, like "Open questions", are left out).
- **Fact recall**: the share of a case's expected facts the report states (see
  ``anvesha.evals.suite`` for how facts are matched).
- **Sub-question coverage**: answered = 1, partial = 0.5, not found = 0, averaged.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from anvesha.evals.suite import Fact
from anvesha.verify.quotes import normalize

_CITATION = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
_GENERATED_SOURCES = re.compile(r"^## Sources\s*$", re.M)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# Sections where uncited sentences are expected: they describe what is *not* known.
_UNCITED_SECTIONS = re.compile(
    r"open questions|limitations|gaps|unknowns|methodology|about this report|next steps", re.I
)
_LIST_MARKER = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+|>\s*)+")
_TABLE_RULE = re.compile(r"^\|?[\s:|-]+\|?$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"“(]?[A-Z0-9])")
_TRAILING_CITATION = re.compile(r"([.!?])\s*(\[\d+(?:\s*,\s*\d+)*\])")
# Sentences shorter than this (without citations) are headings-in-disguise or filler.
MIN_CLAIM_WORDS = 6

COVERAGE_SCORES = {"answered": 1.0, "partial": 0.5, "not_found": 0.0}


def report_body(markdown: str) -> str:
    """The report without the generated sources list (always the last section)."""
    matches = list(_GENERATED_SOURCES.finditer(markdown))
    return markdown[: matches[-1].start()] if matches else markdown


def claim_sentences(markdown: str) -> list[str]:
    """Sentences in the report body that state something (and so should be cited)."""
    sentences: list[str] = []
    skip_level: int | None = None  # inside a section where citations aren't expected
    for raw in report_body(markdown).splitlines():
        line = raw.strip()
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and _UNCITED_SECTIONS.search(heading.group(2)):
                skip_level = level
            continue
        if skip_level is not None or not line or _TABLE_RULE.match(line):
            continue
        if line.startswith(("_", "*")) and line.endswith(("_", "*")) and not line.startswith("**"):
            continue  # an italic note, e.g. why a report is incomplete
        line = _LIST_MARKER.sub("", line).replace("|", " ")
        # "...grew fast. [1] Then..." cites the sentence before it.
        line = _TRAILING_CITATION.sub(r" \2\1", line)
        sentences += [part.strip() for part in _SENTENCE_END.split(line) if part.strip()]
    return [s for s in sentences if len(_CITATION.sub("", s).split()) >= MIN_CLAIM_WORDS]


def cited_sentence_rate(markdown: str) -> float | None:
    sentences = claim_sentences(markdown)
    if not sentences:
        return None
    return sum(1 for s in sentences if _CITATION.search(s)) / len(sentences)


def _normal(text: str) -> str:
    return normalize(text).casefold()


def fact_covered(fact: Fact, text: str) -> bool:
    haystack = _normal(text)
    return all(any(_normal(phrase) in haystack for phrase in group) for group in fact.match)


def fact_recall(markdown: str, facts: Sequence[Fact]) -> tuple[float | None, list[str]]:
    """(share of facts covered, claims of the facts that were missed)."""
    if not facts:
        return None, []
    body = report_body(markdown)
    missed = [fact.claim for fact in facts if not fact_covered(fact, body)]
    return (len(facts) - len(missed)) / len(facts), missed


@dataclass(frozen=True)
class CitedFinding:
    finding_id: str
    status: str  # quote_verified, verified or rejected
    quote_matches: bool  # re-checked against the source text at scoring time


@dataclass(frozen=True)
class CitationScore:
    precision: float | None
    cited: int
    supported: int
    invented: int  # markers for findings that didn't exist (dropped when rendering)
    unreviewed: int  # cited findings the verifier model didn't review


def citation_precision(cited: Sequence[CitedFinding], invented: int) -> CitationScore:
    supported = sum(1 for c in cited if c.quote_matches and c.status != "rejected")
    total = len(cited) + invented
    return CitationScore(
        precision=supported / total if total else None,
        cited=len(cited),
        supported=supported,
        invented=invented,
        unreviewed=sum(1 for c in cited if c.status == "quote_verified"),
    )


def sub_question_coverage(coverages: Iterable[str | None]) -> float | None:
    scores = [COVERAGE_SCORES.get(c or "not_found", 0.0) for c in coverages]
    return sum(scores) / len(scores) if scores else None


def mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None
