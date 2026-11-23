"""The writer: a report built only from verified findings, with citations rendered in code.

When there is nothing to write from, or the budget runs out before writing, the report
is assembled deterministically from the evidence instead, so a run never ends with
nothing to show for what it found.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from anvesha.agents import prompts
from anvesha.agents.citations import render_citations, report_title
from anvesha.agents.context import RunContext
from anvesha.agents.evidence import FindingView, load_findings, load_sub_questions, source_label
from anvesha.db.models import Report, SubQuestion
from anvesha.llm.errors import BudgetExceededError
from anvesha.llm.types import Message
from anvesha.tools.base import untrusted

WRITER_MAX_TOKENS = 8000
QUOTE_CHARS_IN_PROMPT = 500


def _finding_lines(number: int, finding: FindingView) -> str:
    quote = finding.quote
    if len(quote) > QUOTE_CHARS_IN_PROMPT:
        quote = quote[: QUOTE_CHARS_IN_PROMPT - 1] + "…"
    source = source_label(finding)
    if finding.published:
        source += f", {finding.published}"
    if finding.page:
        source += f", page {finding.page}"
    line = f"[F{number}] {finding.statement}\n  Quote: \"{quote}\"\n  Source: {source}"
    if finding.status != "verified":
        line += "\n  (Quote verified in the source; not reviewed by the verifier.)"
    return line


def _evidence_brief(
    sub_questions: list[SubQuestion],
    findings: list[FindingView],
    contradictions: list[dict[str, Any]],
) -> str:
    number = {finding.id: index for index, finding in enumerate(findings, start=1)}
    sections: list[str] = []
    for sq in sub_questions:
        related = [f for f in findings if f.sub_question_id == sq.id]
        block = [f"Sub-question: {sq.question}"]
        block += [_finding_lines(number[f.id], f) for f in related] or ["(no verified findings)"]
        if sq.gaps:
            block.append(f"Known gaps: {'; '.join(sq.gaps)}")
        sections.append("\n".join(block))
    orphans = [f for f in findings if f.sub_question_id not in {sq.id for sq in sub_questions}]
    if orphans:
        sections.append("\n".join(_finding_lines(number[f.id], f) for f in orphans))
    by_id = {str(f.id): number[f.id] for f in findings}
    if contradictions:
        lines = ["Contradictions noted by the lead researcher:"]
        for item in contradictions:
            refs = ", ".join(f"F{by_id[i]}" for i in item.get("finding_ids", []) if i in by_id)
            lines.append(f"- {item.get('description', '')}" + (f" ({refs})" if refs else ""))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _digest_report(
    question: str, sub_questions: list[SubQuestion], findings: list[FindingView], reason: str
) -> str:
    """A plain report listing the verified findings, used when the writer can't run."""
    number = {finding.id: index for index, finding in enumerate(findings, start=1)}
    lines = [f"# {question}", "", f"_{reason}_", "", "## Findings", ""]
    for sq in sub_questions:
        related = [f for f in findings if f.sub_question_id == sq.id]
        if not related:
            continue
        lines += [f"### {sq.question}", ""]
        lines += [f"- {f.statement} [F{number[f.id]}]" for f in related]
        lines.append("")
    return "\n".join(lines)


def _no_evidence_report(question: str, sub_questions: list[SubQuestion], reason: str) -> str:
    lines = [
        f"# {question}",
        "",
        f"No verified evidence was found, so no conclusions are drawn. {reason}",
        "",
        "## What was investigated",
        "",
    ]
    for sq in sub_questions:
        lines.append(f"- **{sq.question}** ({sq.coverage or sq.status})")
        if sq.summary:
            lines.append(f"  {sq.summary}")
        for gap in sq.gaps:
            lines.append(f"  - Gap: {gap}")
    return "\n".join(lines) + "\n"


async def write_report(
    ctx: RunContext, *, contradictions: list[dict[str, Any]], stop_reason: str | None
) -> tuple[uuid.UUID, str]:
    """Write and save the run's report. Returns (report id, status)."""
    ctx.budget.usable_fraction = Decimal(1)  # the reserve kept for writing is now available
    sub_questions = await load_sub_questions(ctx)
    findings = await load_findings(ctx)
    status = "incomplete" if stop_reason else "complete"
    details: dict[str, Any] = {
        "findings_available": len(findings),
        "sub_questions": len(sub_questions),
        "stop_reason": stop_reason,
    }
    if not findings:
        draft = _no_evidence_report(
            ctx.question, sub_questions, "The researchers' notes are below."
        )
        status = "incomplete"
        details["mode"] = "no_evidence"
    else:
        await ctx.check_cancelled(force=True)
        await ctx.emit("stage", {"stage": "writing", "findings": len(findings)}, agent="writer")
        messages = [
            Message.system(prompts.WRITER.format(today=datetime.now(UTC).date().isoformat())),
            Message.user(
                f"Research question:\n{ctx.question}\n\nVerified findings (cite as [F#]):\n"
                + untrusted("verified_findings", _evidence_brief(sub_questions, findings,
                                                                contradictions))
            ),
        ]
        draft = ""
        fallback_reason: str | None = None
        try:
            completion = await ctx.complete("writer", messages, max_tokens=WRITER_MAX_TOKENS)
            draft = completion.text.strip()
            if completion.finish_reason in ("length", "max_tokens"):
                details["truncated"] = True
                status = "incomplete"
            if not draft:
                fallback_reason = "The writer model returned an empty report"
        except BudgetExceededError:
            fallback_reason = "The budget ran out before the report could be written"
        details["mode"] = "written"
        if fallback_reason:
            details["mode"] = "digest"
            status = "incomplete"
            draft = _digest_report(
                ctx.question,
                sub_questions,
                findings,
                f"{fallback_reason}, so this is a list of the verified findings.",
            )

    rendered = render_citations(draft, findings)
    details["findings_cited"] = len(rendered.cited_findings)
    if rendered.unknown_markers:
        details["dropped_citations"] = sorted(set(rendered.unknown_markers))
    if findings and not rendered.cited_findings:
        status = "incomplete"
        details["warning"] = "The report cites none of the findings."
    title = report_title(rendered.markdown, ctx.question)
    report_id = await _save(ctx, title, rendered.markdown, rendered.citations, details, status)
    return report_id, status


async def _save(
    ctx: RunContext,
    title: str,
    markdown: str,
    citations: list[dict[str, object]],
    details: dict[str, Any],
    status: str,
) -> uuid.UUID:
    """Insert the report, or replace it if this run already wrote one (a retried step)."""
    async with ctx.db.session() as session:
        # One active run per project, so two reports never compete for a version number.
        version = (
            await session.scalar(
                select(func.coalesce(func.max(Report.version), 0)).where(
                    Report.project_id == ctx.project_id, Report.run_id != ctx.run_id
                )
            )
            or 0
        ) + 1
        statement = insert(Report).values(
            workspace_id=ctx.workspace_id,
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            version=version,
            status=status,
            title=title,
            markdown=markdown,
            citations=citations,
            details=details,
        )
        report_id = await session.scalar(
            statement.on_conflict_do_update(
                constraint="uq_reports_run_id",
                set_={
                    "status": statement.excluded.status,
                    "title": statement.excluded.title,
                    "markdown": statement.excluded.markdown,
                    "citations": statement.excluded.citations,
                    "details": statement.excluded.details,
                    "updated_at": func.now(),
                },
            ).returning(Report.id)
        )
        await session.commit()
    assert report_id is not None
    return report_id
