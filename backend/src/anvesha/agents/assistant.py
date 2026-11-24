"""The Research Assistant: answers questions about a project from its evidence.

It sees the latest run's verified findings and can search and read the project's saved
sources. It cannot browse the web: new evidence comes from research runs. The same rule
as reports applies: every claim cites a quote that was checked word for word in code
(findings already were; new quotes go through the ``cite`` tool), and code, not the
model, turns finding markers into numbered citations.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from anvesha.agents import prompts
from anvesha.agents.citations import render_citations
from anvesha.agents.context import WorkspaceCompleter
from anvesha.agents.evidence import FindingView
from anvesha.agents.researcher import quote_hash, trim_old_tool_outputs
from anvesha.agents.structured import validation_problems
from anvesha.errors import AppError
from anvesha.events import clip
from anvesha.ingest.extract import page_for_offset
from anvesha.llm.errors import BudgetExceededError, LLMInvalidResponseError
from anvesha.llm.types import Completion, Message, ToolCall, ToolSpec
from anvesha.tools import ToolContext, ToolRegistry
from anvesha.tools.base import tool_parameters_schema, untrusted

logger = logging.getLogger(__name__)

# Read-only tools over the project's saved sources.
ASSISTANT_TOOLS = ["search_sources", "read_source", "list_sources"]
ASSISTANT_MAX_TOKENS = 2500
# Earlier turns included in the prompt, and how much of each.
HISTORY_TURNS = 6
HISTORY_CHARS = 3000
MAX_FINDINGS_IN_PROMPT = 80
REPORT_CHARS = 6000
# Share of the reply's budget for searching and reading; the rest is kept for answering.
TOOL_BUDGET_FRACTION = Decimal("0.8")

# Rendered citation numbers like [1] or [2, 5] in earlier replies and in reports. They'd
# be confused with the finding numbers of this turn, so they're removed from context.
_RENDERED_CITATION = re.compile(r"\s?\[\d+(?:,\s*\d+)*\]")
_SOURCES_SECTION = re.compile(r"\n#{1,3}\s*sources\s*\n.*", re.I | re.S)

Progress = Callable[[dict[str, Any]], Awaitable[None]]


class CiteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: uuid.UUID = Field(description="The saved source the quote comes from.")
    quote: Annotated[str, StringConstraints(min_length=1, max_length=2500)] = Field(
        description="The passage, copied exactly from the source. Use ... to skip words."
    )


CITE_SPEC = ToolSpec(
    "cite",
    "Check a quote from a saved source word for word. If it appears in the source, returns "
    "a finding number to cite in your answer.",
    tool_parameters_schema(CiteArgs),
)


@dataclass
class Turn:
    question: str
    answer: str


@dataclass
class AssistantReply:
    content: str
    citations: list[dict[str, Any]]
    details: dict[str, Any] = field(default_factory=dict)


def strip_rendered_citations(markdown: str) -> str:
    return _RENDERED_CITATION.sub("", _SOURCES_SECTION.sub("", markdown)).strip()


def _evidence_block(findings: list[FindingView], report: str | None) -> str:
    lines = []
    for number, finding in enumerate(findings[:MAX_FINDINGS_IN_PROMPT], start=1):
        source = finding.source_title or finding.source_url or finding.source_filename or "?"
        page = f", page {finding.page}" if finding.page else ""
        lines.append(
            f"[F{number}] {finding.statement}\n"
            f'  Quote: "{finding.quote}"\n  Source: {source} ({finding.source_id}{page})'
        )
    parts = [
        "Verified findings:\n" + ("\n".join(lines) if lines else "(no findings yet)"),
    ]
    if report:
        text = strip_rendered_citations(report)
        if len(text) > REPORT_CHARS:
            text = text[:REPORT_CHARS] + "\n[Report shortened.]"
        parts.append("The latest report (for context; cite findings, not the report):\n" + text)
    return untrusted("project_evidence", "\n\n".join(parts))


class Assistant:
    def __init__(
        self,
        *,
        completer: WorkspaceCompleter,
        tools: ToolRegistry,
        tool_context: ToolContext,
        max_steps: int,
        on_progress: Progress | None = None,
    ) -> None:
        self.completer = completer
        self.tools = tools
        self.tool_context = tool_context
        self.max_steps = max_steps
        self.on_progress = on_progress
        self.findings: list[FindingView] = []
        self._cited_hashes: dict[tuple[uuid.UUID, str], int] = {}
        self._tool_counts: dict[str, int] = {}

    async def reply(
        self,
        *,
        objective: str,
        question: str,
        history: list[Turn],
        findings: list[FindingView],
        report: str | None,
    ) -> AssistantReply:
        self.findings = list(findings[:MAX_FINDINGS_IN_PROMPT])
        for number, finding in enumerate(self.findings, start=1):
            self._cited_hashes[(finding.source_id, quote_hash(finding.quote))] = number
        messages = [
            Message.system(
                prompts.ASSISTANT.format(
                    untrusted_rule=prompts.UNTRUSTED_RULE,
                    objective=objective,
                    today=datetime.now(UTC).date().isoformat(),
                )
            )
        ]
        for turn in history[-HISTORY_TURNS:]:
            messages.append(Message.user(clip_block(turn.question)))
            messages.append(Message.assistant(clip_block(strip_rendered_citations(turn.answer))))
        messages.append(
            Message.user(f"{_evidence_block(self.findings, report)}\n\nMy question:\n{question}")
        )
        specs = [*self.tools.specs(ASSISTANT_TOOLS), CITE_SPEC]
        budget = self.completer.budget
        budget.usable_fraction = TOOL_BUDGET_FRACTION
        answer = ""
        steps = 0
        for step in range(self.max_steps):
            final = step == self.max_steps - 1
            if final:
                messages.append(
                    Message.user("Write your answer now, with citations. Don't call more tools.")
                )
                budget.usable_fraction = Decimal(1)
            try:
                completion = await self._complete(messages, specs)
            except BudgetExceededError:
                if final:
                    raise
                # Out of budget for searching: answer from what has been found so far.
                final = True
                budget.usable_fraction = Decimal(1)
                messages.append(
                    Message.user(
                        "The budget for searching is used up. Write your answer now from what "
                        "you have, with citations. Don't call more tools."
                    )
                )
                completion = await self._complete(messages, specs)
            steps = step + 1
            messages.append(Message.assistant(completion.text, completion.tool_calls))
            if final or not completion.tool_calls:
                answer = completion.text.strip()
                if answer or final:
                    break
                messages.append(Message.user("Please write your answer to my question."))
                continue
            results = await asyncio.gather(*(self._handle(call) for call in completion.tool_calls))
            for call, content in zip(completion.tool_calls, results, strict=True):
                messages.append(Message.tool_result(call.id, content))
        if not answer:
            raise LLMInvalidResponseError(
                "The assistant didn't write an answer. Try asking a narrower question."
            )
        return self._render(answer, steps)

    async def _complete(self, messages: list[Message], specs: list[ToolSpec]) -> Completion:
        return await self.completer.complete(
            "lead", trim_old_tool_outputs(messages), tools=specs, max_tokens=ASSISTANT_MAX_TOKENS
        )

    def _render(self, answer: str, steps: int) -> AssistantReply:
        rendered = render_citations(answer, self.findings, sources_section=False)
        by_id = {str(finding.id): finding for finding in self.findings}
        citations = []
        for citation in rendered.citations:
            quotes = []
            for finding_id in cast(list[str], citation["finding_ids"]):
                finding = by_id[finding_id]
                quotes.append(
                    {
                        "quote": finding.quote,
                        "statement": finding.statement or None,
                        "page": finding.page,
                        # Set for findings from a run; quotes cited in chat aren't stored.
                        "finding_id": finding_id if finding.status != "cited" else None,
                    }
                )
            citations.append(
                {
                    "number": citation["number"],
                    "source_id": citation["source_id"],
                    "title": citation["title"],
                    "url": citation["url"],
                    "quotes": quotes,
                }
            )
        budget = self.completer.budget
        return AssistantReply(
            content=rendered.markdown.strip(),
            citations=citations,
            details={
                "steps": steps,
                "tools": self._tool_counts,
                "unknown_markers": rendered.unknown_markers,
                "calls": budget.calls,
                "cost_usd": str(budget.spent_usd),
                "unpriced_calls": budget.unpriced_calls,
            },
        )

    async def _handle(self, call: ToolCall) -> str:
        """Run one tool call and return what the model reads. Never raises."""
        self._tool_counts[call.name] = self._tool_counts.get(call.name, 0) + 1
        try:
            if self.on_progress is not None:
                await self.on_progress(
                    {"tool": call.name, "detail": _describe(call.name, call.arguments)}
                )
            if call.name == CITE_SPEC.name:
                return await self._cite(call)
            if call.name not in ASSISTANT_TOOLS:
                return f"Error: there is no tool named {call.name!r}."
            result = await self.tools.invoke(call.name, call.arguments, self.tool_context)
            return result.content
        except Exception:
            logger.exception("Assistant tool %s failed", call.name)
            return f"Error: the {call.name} tool failed unexpectedly. Try something else."

    async def _cite(self, call: ToolCall) -> str:
        try:
            args = CiteArgs.model_validate(call.arguments)
        except ValidationError as exc:
            return f"Invalid arguments: {validation_problems(exc)}."
        key = (args.source_id, quote_hash(args.quote))
        if key in self._cited_hashes:
            return f"Already available: cite it as [F{self._cited_hashes[key]}]."
        context = self.tool_context
        try:
            source, match = await context.sources.verify_quote(
                context.workspace_id, context.project_id, args.source_id, args.quote
            )
        except AppError as exc:
            return f"Error ({exc.code}): {exc}"
        if match.status == "too_short":
            return "REJECTED: the quote is too short to verify. Quote at least a full clause."
        if not match.supported:
            return (
                f"REJECTED: the quote does not appear in source {source.id}. Copy the passage "
                "exactly as it appears (search_sources or read_source show the text)."
            )
        page = page_for_offset(source.details, match.spans[0][0])
        self.findings.append(
            FindingView(
                id=uuid.uuid4(),
                sub_question_id=None,
                statement="",
                quote=args.quote,
                status="cited",
                judge_note=None,
                page=page,
                source_id=source.id,
                source_title=source.title,
                source_url=source.url,
                source_filename=source.filename,
                published=source.published,
                site_name=source.site_name,
            )
        )
        number = len(self.findings)
        self._cited_hashes[key] = number
        return f"Quote verified. Cite it as [F{number}]."


def clip_block(text: str, limit: int = HISTORY_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe(tool: str, arguments: dict[str, Any]) -> str:
    """A short, human-readable line for the chat's progress indicator."""
    if tool == "search_sources":
        return f"Searching saved sources for “{clip(str(arguments.get('query', '')), 80)}”"
    if tool == "read_source":
        return "Reading a saved source"
    if tool == "list_sources":
        return "Listing saved sources"
    if tool == "cite":
        return "Checking a quote"
    return f"Using {tool}"
