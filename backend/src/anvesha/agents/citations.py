"""Turn the writer's finding markers ([F3], [F2, F5]) into numbered source citations.

The model only ever refers to findings it was given; code decides what each citation
points to. Markers for findings that don't exist are dropped, and the source list is
generated here, never by the model.
"""

import re
from dataclasses import dataclass, field

from anvesha.agents.evidence import FindingView, source_label

# [F1], [F1, F2], [F1; F2], [F1,F2] (the writer is asked for the first two forms).
# The optional leading space goes with the marker, so dropping a bad marker leaves no gap.
_MARKER = re.compile(r"([ \t]?)\[\s*(F\d+(?:\s*[,;]\s*F\d+)*)\s*\]")
_LABEL = re.compile(r"F(\d+)")
_SOURCES_HEADING = re.compile(r"^#{1,3}\s*(sources|references|bibliography)\s*$", re.I | re.M)


@dataclass
class RenderedReport:
    markdown: str
    citations: list[dict[str, object]]
    cited_findings: set[str] = field(default_factory=set)
    unknown_markers: list[str] = field(default_factory=list)


def _source_entry(number: int, finding: FindingView) -> str:
    parts = [f"{number}. {source_label(finding)}"]
    details = [d for d in (finding.site_name, finding.published) if d]
    if details:
        parts.append(f"({', '.join(details)})")
    if finding.source_url:
        parts.append(f"<{finding.source_url}>")
    elif finding.source_filename:
        parts.append(f"(uploaded file: {finding.source_filename})")
    return " ".join(parts)


def render_citations(draft: str, findings: list[FindingView]) -> RenderedReport:
    """``findings[i]`` is the writer's ``F{i+1}``."""
    by_label = {f"F{index}": finding for index, finding in enumerate(findings, start=1)}
    source_numbers: dict[str, int] = {}
    source_findings: dict[str, list[str]] = {}
    first_finding: dict[str, FindingView] = {}
    report = RenderedReport(markdown="", citations=[])

    # A model-written sources section would duplicate (and could contradict) ours.
    heading = _SOURCES_HEADING.search(draft)
    if heading:
        draft = draft[: heading.start()].rstrip()

    def replace(match: re.Match[str]) -> str:
        numbers: list[int] = []
        for label in (f"F{n}" for n in _LABEL.findall(match.group(2))):
            finding = by_label.get(label)
            if finding is None:
                report.unknown_markers.append(label)
                continue
            key = str(finding.source_id)
            if key not in source_numbers:
                source_numbers[key] = len(source_numbers) + 1
                source_findings[key] = []
                first_finding[key] = finding
            if str(finding.id) not in source_findings[key]:
                source_findings[key].append(str(finding.id))
            report.cited_findings.add(str(finding.id))
            if source_numbers[key] not in numbers:
                numbers.append(source_numbers[key])
        if not numbers:
            return ""
        return match.group(1) + "[" + ", ".join(str(n) for n in sorted(numbers)) + "]"

    body = _MARKER.sub(replace, draft).strip()

    entries: list[str] = []
    for key, number in source_numbers.items():
        finding = first_finding[key]
        entries.append(_source_entry(number, finding))
        report.citations.append(
            {
                "number": number,
                "source_id": key,
                "title": finding.source_title,
                "url": finding.source_url,
                "finding_ids": source_findings[key],
            }
        )
    if entries:
        body += "\n\n## Sources\n\n" + "\n".join(entries)
    report.markdown = body + "\n"
    return report


def report_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            title = line[2:].strip().strip("#").strip()
            if title:
                return title[:500]
    return fallback[:500]
