"""The lead researcher: plans the research, then reviews the evidence for gaps."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from anvesha.agents import prompts
from anvesha.agents.context import RunContext
from anvesha.agents.evidence import FindingView, source_label
from anvesha.agents.structured import SubmitTool, structured_call
from anvesha.db.models import Source, SubQuestion
from anvesha.llm.types import Message
from anvesha.tools.base import untrusted

MAX_SOURCES_IN_PROMPT = 40


class _Output(BaseModel):
    # Lenient with extra keys: models sometimes add fields; they're simply ignored.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PlannedQuestion(_Output):
    question: str = Field(min_length=8, max_length=600, description="A self-contained question.")
    rationale: str = Field(
        default="", max_length=1000, description="Why it matters for the main question."
    )


class Plan(_Output):
    strategy: str = Field(default="", max_length=2000, description="Overall source strategy.")
    sub_questions: list[PlannedQuestion] = Field(min_length=1, max_length=12)


class Contradiction(_Output):
    description: str = Field(min_length=1, max_length=1000)
    findings: list[int] = Field(
        default_factory=list, description="Numbers of the conflicting findings."
    )


class Review(_Output):
    assessment: str = Field(default="", max_length=4000)
    contradictions: list[Contradiction] = Field(default_factory=list, max_length=12)
    follow_up_questions: list[PlannedQuestion] = Field(default_factory=list, max_length=12)


SUBMIT_PLAN = SubmitTool(
    "submit_plan", "Submit the research plan: a source strategy and the sub-questions.", Plan
)
SUBMIT_REVIEW = SubmitTool(
    "submit_review",
    "Submit the review: assessment, contradictions, and follow-up sub-questions (if any).",
    Review,
)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def dedupe_questions(
    questions: list[PlannedQuestion], limit: int, existing: set[str] | None = None
) -> list[PlannedQuestion]:
    seen = set(existing or ())
    unique: list[PlannedQuestion] = []
    for item in questions:
        key = " ".join(item.question.casefold().split())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:limit]


async def make_plan(ctx: RunContext) -> Plan:
    async with ctx.db.session() as session:
        sources = (
            await session.execute(
                select(Source.title, Source.url, Source.filename)
                .where(Source.workspace_id == ctx.workspace_id, Source.project_id == ctx.project_id)
                .order_by(Source.created_at.desc())
                .limit(MAX_SOURCES_IN_PROMPT)
            )
        ).all()
    request = f"Research question:\n{ctx.question}"
    if sources:
        listing = "\n".join(f"- {t or u or f or 'untitled'}" for t, u, f in sources)
        request += (
            "\n\nThe project already has these saved sources (researchers can search them):\n"
            + untrusted("project_sources", listing)
        )
    messages = [
        Message.system(
            prompts.LEAD_PLAN.format(
                max_sub_questions=ctx.profile.max_sub_questions,
                untrusted_rule=prompts.UNTRUSTED_RULE,
                today=_today(),
            )
        ),
        Message.user(request),
    ]
    plan = await structured_call(
        ctx, role="lead", messages=messages, tool=SUBMIT_PLAN, max_tokens=3000
    )
    plan.sub_questions = dedupe_questions(plan.sub_questions, ctx.profile.max_sub_questions)
    return plan


def _render_progress(sub_questions: list[SubQuestion], findings: list[FindingView]) -> str:
    number = {finding.id: index for index, finding in enumerate(findings, start=1)}
    lines: list[str] = []
    for index, sq in enumerate(sub_questions, start=1):
        lines.append(f"Sub-question {index} (round {sq.round}): {sq.question}")
        lines.append(f"  Coverage: {sq.coverage or sq.status}")
        if sq.summary:
            lines.append(f"  Researcher summary: {sq.summary}")
        if sq.gaps:
            lines.append(f"  Gaps: {'; '.join(sq.gaps)}")
        related = [f for f in findings if f.sub_question_id == sq.id]
        for finding in related:
            lines.append(
                f"  [{number[finding.id]}] {finding.statement} (source: {source_label(finding)})"
            )
        if not related:
            lines.append("  No verified findings.")
        lines.append("")
    return "\n".join(lines).strip()


async def review_progress(
    ctx: RunContext,
    sub_questions: list[SubQuestion],
    findings: list[FindingView],
    *,
    max_follow_ups: int,
) -> tuple[Review, list[dict[str, object]]]:
    """The lead's review, plus contradictions with finding numbers resolved to ids."""
    messages = [
        Message.system(
            prompts.LEAD_REFLECT.format(
                max_follow_ups=max_follow_ups, untrusted_rule=prompts.UNTRUSTED_RULE
            )
        ),
        Message.user(
            f"Research question:\n{ctx.question}\n\nProgress so far "
            "(summaries and findings were written from outside sources):\n"
            + untrusted("research_progress", _render_progress(sub_questions, findings))
        ),
    ]
    review = await structured_call(
        ctx, role="lead", messages=messages, tool=SUBMIT_REVIEW, max_tokens=3000
    )
    asked = {" ".join(sq.question.casefold().split()) for sq in sub_questions}
    review.follow_up_questions = dedupe_questions(
        review.follow_up_questions, max_follow_ups, existing=asked
    )
    contradictions: list[dict[str, object]] = []
    for item in review.contradictions:
        ids = [str(findings[n - 1].id) for n in item.findings if 1 <= n <= len(findings)]
        contradictions.append({"description": item.description, "finding_ids": ids})
    return review, contradictions
