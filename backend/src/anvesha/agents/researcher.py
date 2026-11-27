"""A researcher agent: a tool-calling loop that investigates one sub-question.

The agent searches, reads, and records findings. Every finding's quote is checked
against the saved source text the moment it is recorded, so the agent learns right away
when it paraphrased and can fix the quote while the source is still in front of it.
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from anvesha import tracing
from anvesha.agents import prompts
from anvesha.agents.context import RunContext
from anvesha.agents.structured import SubmitTool, validation_problems
from anvesha.db.models import Finding, SubQuestion
from anvesha.errors import AppError
from anvesha.events import clip
from anvesha.ingest.extract import page_for_offset
from anvesha.llm.errors import BudgetExceededError, LLMError, LLMInvalidResponseError
from anvesha.llm.types import Message, ToolCall
from anvesha.verify.quotes import normalize

logger = logging.getLogger(__name__)

# Shared tools a researcher may use (least privilege: nothing that deletes or lists keys).
RESEARCH_TOOLS = ["web_search", "scholar_search", "fetch_page", "read_source", "search_sources"]
# Tool outputs older than this many tool messages are trimmed to keep the context small.
KEEP_RECENT_TOOL_OUTPUTS = 6
TRIMMED_PREVIEW_CHARS = 400
RESEARCHER_MAX_TOKENS = 2048


class RecordFindingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    statement: Annotated[str, StringConstraints(min_length=10, max_length=1000)] = Field(
        description="A concise, self-contained claim, supported by the quote alone."
    )
    quote: Annotated[str, StringConstraints(min_length=1, max_length=2500)] = Field(
        description="The supporting passage, copied exactly from the source. Use ... to "
        "skip words within a passage."
    )
    source_id: uuid.UUID = Field(description="The saved source the quote comes from.")


class FinishArgs(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    summary: Annotated[str, StringConstraints(min_length=1, max_length=4000)] = Field(
        description="What the evidence shows for this sub-question, in a few sentences."
    )
    coverage: Literal["answered", "partial", "not_found"] = Field(
        description="answered: well supported; partial: some evidence; not_found: none."
    )
    gaps: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=8, description="What is still unknown."
    )


RECORD_FINDING = SubmitTool(
    "record_finding",
    "Record one piece of evidence: a statement plus the exact quote from a saved source "
    "that supports it. The quote is verified word for word; fix and retry if rejected.",
    RecordFindingArgs,
)
FINISH = SubmitTool(
    "finish",
    "Finish this sub-question with a summary of what the evidence shows.",
    FinishArgs,
)


@dataclass
class ResearchOutcome:
    coverage: str = "partial"
    summary: str = ""
    gaps: list[str] = field(default_factory=list)
    steps: int = 0
    findings: int = 0
    stopped: str | None = None  # why the agent stopped early, if it did


def quote_hash(quote: str) -> str:
    return hashlib.sha256(normalize(quote).encode()).hexdigest()


def trim_old_tool_outputs(
    messages: list[Message], keep: int = KEEP_RECENT_TOOL_OUTPUTS
) -> list[Message]:
    """Shorten all but the most recent tool outputs; the agent can re-read sources."""
    tool_positions = [i for i, m in enumerate(messages) if m.role == "tool"]
    to_trim = set(tool_positions[:-keep]) if keep else set(tool_positions)
    trimmed: list[Message] = []
    for index, message in enumerate(messages):
        if index in to_trim and len(message.content) > TRIMMED_PREVIEW_CHARS + 100:
            message = replace(
                message,
                content=message.content[:TRIMMED_PREVIEW_CHARS]
                + "\n[Older tool output shortened to save space. Use read_source or "
                "search_sources to see it again.]",
            )
        trimmed.append(message)
    return trimmed


class Researcher:
    def __init__(self, ctx: RunContext, sub_question: dict[str, Any], label: str) -> None:
        self.ctx = ctx
        self.sub_question_id = uuid.UUID(sub_question["id"])
        self.question = str(sub_question["question"])
        self.rationale = str(sub_question.get("rationale") or "")
        self.label = label
        self.outcome = ResearchOutcome()
        self._finished = False

    async def run(self, other_questions: list[str]) -> ResearchOutcome:
        ctx, profile = self.ctx, self.ctx.profile
        await self._set_status("researching")
        await ctx.emit(
            "researcher_started",
            {"sub_question_id": str(self.sub_question_id), "question": self.question},
            agent=self.label,
        )
        specs = [*ctx.tools.specs(RESEARCH_TOOLS), RECORD_FINDING.spec(), FINISH.spec()]
        task = f"Main research question:\n{ctx.question}\n\nYour sub-question:\n{self.question}"
        if self.rationale:
            task += f"\n\nWhy it matters: {self.rationale}"
        if other_questions:
            task += "\n\nOther researchers are covering (avoid duplicating them):\n" + "\n".join(
                f"- {q}" for q in other_questions
            )
        messages = [
            Message.system(
                prompts.RESEARCHER.format(
                    max_steps=profile.researcher_max_steps,
                    max_findings=profile.max_findings_per_researcher,
                    untrusted_rule=prompts.UNTRUSTED_RULE,
                    today=datetime.now(UTC).date().isoformat(),
                )
            ),
            Message.user(task),
        ]
        prose_replies = 0
        try:
            for step in range(profile.researcher_max_steps):
                await ctx.check_cancelled()
                if step == profile.researcher_max_steps - 1:
                    messages.append(
                        Message.user(
                            "This is your last step. Record any remaining findings and call "
                            "`finish` now."
                        )
                    )
                completion = await ctx.complete(
                    "researcher",
                    trim_old_tool_outputs(messages),
                    tools=specs,
                    max_tokens=RESEARCHER_MAX_TOKENS,
                )
                self.outcome.steps = step + 1
                messages.append(Message.assistant(completion.text, completion.tool_calls))
                if completion.text.strip():
                    await ctx.emit(
                        "agent_message", {"text": clip(completion.text)}, agent=self.label
                    )
                if not completion.tool_calls:
                    prose_replies += 1
                    if prose_replies > 1:
                        self.outcome.summary = clip(completion.text, 4000)
                        self.outcome.stopped = "no_tool_calls"
                        break
                    messages.append(
                        Message.user(
                            "Continue with tools: record findings with `record_finding`, and "
                            "call `finish` when you are done."
                        )
                    )
                    continue
                results = await asyncio.gather(
                    *(self._handle(call) for call in completion.tool_calls)
                )
                for call, content in zip(completion.tool_calls, results, strict=True):
                    messages.append(Message.tool_result(call.id, content))
                if self._finished:
                    break
            else:
                self.outcome.stopped = "step_limit"
        except BudgetExceededError as exc:
            self.outcome.stopped = "budget"
            await ctx.emit(
                "researcher_stopped", {"reason": "budget", "detail": str(exc)}, agent=self.label
            )
        except LLMError as exc:
            # Temporary provider trouble (after the adapter's own retries) or a broken
            # response ends this researcher, not the run. Problems that would hit every
            # agent (bad key, unknown model) propagate and fail the run instead.
            if not (exc.retryable or isinstance(exc, LLMInvalidResponseError)):
                raise
            self.outcome.stopped = "error"
            self.outcome.gaps.append(f"Research stopped early: {exc.code}")
            await ctx.emit(
                "researcher_stopped",
                {"reason": exc.code, "detail": clip(str(exc))},
                agent=self.label,
            )
        if not self._finished and not self.outcome.summary:
            self.outcome.summary = (
                f"Stopped before finishing ({self.outcome.stopped or 'unknown'}). "
                f"{self.outcome.findings} finding(s) recorded."
            )
            self.outcome.coverage = "partial" if self.outcome.findings else "not_found"
        await self._save_outcome()
        await ctx.emit(
            "researcher_finished",
            {
                "sub_question_id": str(self.sub_question_id),
                "coverage": self.outcome.coverage,
                "findings": self.outcome.findings,
                "steps": self.outcome.steps,
                "stopped": self.outcome.stopped,
                "summary": clip(self.outcome.summary),
            },
            agent=self.label,
        )
        return self.outcome

    async def _handle(self, call: ToolCall) -> str:
        """Run one tool call and return what the model reads. Never raises."""
        try:
            if call.name not in (FINISH.name, RECORD_FINDING.name):
                return await self._dispatch(call)  # registry tools trace themselves
            with tracing.span(call.name, kind="tool", input=call.arguments) as span:
                content = await self._dispatch(call)
                outcome = (
                    "rejected"
                    if content.startswith("REJECTED")
                    else "recorded"
                    if content.startswith("Recorded")
                    else None
                )
                tracing.set_attributes(span, {"anvesha.finding.outcome": outcome})
                tracing.set_output(span, content)
                return content
        except Exception:
            logger.exception("Researcher tool %s failed in run %s", call.name, self.ctx.run_id)
            return f"Error: the {call.name} tool failed unexpectedly. Try something else."

    async def _dispatch(self, call: ToolCall) -> str:
        ctx = self.ctx
        if call.name == FINISH.name:
            return self._finish(call)
        if call.name == RECORD_FINDING.name:
            return await self._record_finding(call)
        if call.name not in RESEARCH_TOOLS:
            return f"Error: there is no tool named {call.name!r}."
        await ctx.emit(
            "tool_call", {"tool": call.name, "arguments": _brief(call.arguments)}, agent=self.label
        )
        result = await ctx.tools.invoke(call.name, call.arguments, ctx.tool_context)
        await ctx.emit(
            "tool_result",
            {
                "tool": call.name,
                "ok": not result.is_error,
                "error_code": result.error_code,
                "summary": clip(result.content.split("\n", 1)[0], 200),
            },
            agent=self.label,
        )
        return result.content

    def _finish(self, call: ToolCall) -> str:
        try:
            args = FINISH.parse(call.arguments)
        except ValidationError as exc:
            return f"Invalid arguments: {validation_problems(exc)}. Call `finish` again."
        self.outcome.summary = args.summary
        self.outcome.coverage = args.coverage
        self.outcome.gaps = [*self.outcome.gaps, *args.gaps]
        self._finished = True
        return "Finished."

    async def _record_finding(self, call: ToolCall) -> str:
        ctx = self.ctx
        try:
            args = RECORD_FINDING.parse(call.arguments)
        except ValidationError as exc:
            return f"Invalid arguments: {validation_problems(exc)}."
        if self.outcome.findings >= ctx.profile.max_findings_per_researcher:
            return (
                f"Finding limit ({ctx.profile.max_findings_per_researcher}) reached. Call "
                "`finish` now."
            )
        try:
            source, match = await ctx.sources.verify_quote(
                ctx.workspace_id, ctx.project_id, args.source_id, args.quote
            )
        except AppError as exc:
            return f"Error ({exc.code}): {exc}"
        if match.status == "too_short":
            return (
                "REJECTED: the quote is too short to verify. Quote at least a full clause "
                "(4+ words, 20+ characters)."
            )
        if not match.supported:
            await ctx.emit(
                "finding_rejected",
                {"source_id": str(source.id), "statement": clip(args.statement, 200)},
                agent=self.label,
            )
            return (
                f"REJECTED: the quote does not appear in source {source.id}. Copy the passage "
                "exactly as it appears (read_source or search_sources show the text); don't "
                "paraphrase or fix typos. Use ... to skip words."
            )
        page = page_for_offset(source.details, match.spans[0][0])
        async with ctx.db.session() as session:
            finding_id = await session.scalar(
                insert(Finding)
                .values(
                    workspace_id=ctx.workspace_id,
                    project_id=ctx.project_id,
                    run_id=ctx.run_id,
                    sub_question_id=self.sub_question_id,
                    source_id=source.id,
                    statement=args.statement,
                    quote=args.quote,
                    quote_hash=quote_hash(args.quote),
                    spans=[list(span) for span in match.spans],
                    page=page,
                    status="quote_verified",
                )
                .on_conflict_do_nothing(constraint="uq_findings_run_id")
                .returning(Finding.id)
            )
            await session.commit()
        if finding_id is None:
            return "This quote from this source is already recorded; no need to repeat it."
        self.outcome.findings += 1
        await ctx.emit(
            "finding_recorded",
            {
                "finding_id": str(finding_id),
                "sub_question_id": str(self.sub_question_id),
                "source_id": str(source.id),
                "source_title": clip(source.title, 200),
                "statement": clip(args.statement, 300),
                "page": page,
            },
            agent=self.label,
        )
        where = f", page {page}" if page else ""
        return f"Recorded finding {finding_id} (quote verified in source {source.id}{where})."

    async def _set_status(self, status: str) -> None:
        async with self.ctx.db.session() as session:
            await session.execute(
                update(SubQuestion)
                .where(SubQuestion.id == self.sub_question_id)
                .values(status=status)
            )
            await session.commit()

    async def _save_outcome(self) -> None:
        async with self.ctx.db.session() as session:
            await session.execute(
                update(SubQuestion)
                .where(SubQuestion.id == self.sub_question_id)
                .values(
                    status="done",
                    coverage=self.outcome.coverage,
                    summary=self.outcome.summary,
                    gaps=self.outcome.gaps[:10],
                    steps=self.outcome.steps,
                    error=self.outcome.stopped,
                )
            )
            await session.commit()


def _brief(arguments: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments for the activity log, with long values shortened."""
    return {
        key: clip(value, 200) if isinstance(value, str) else value
        for key, value in list(arguments.items())[:6]
    }
