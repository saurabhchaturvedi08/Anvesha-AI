"""The verifier: double-checks findings before anything is written from them.

1. Deterministic: the quote must still be found in the saved source text (code, not AI).
2. A judge model checks that the quote, in context, supports the statement. The judge
   can only reject; it can never turn a quote that isn't in the source into evidence.
"""

import asyncio
import logging
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from anvesha.agents import prompts
from anvesha.agents.context import RunContext
from anvesha.agents.structured import SubmitTool, structured_call
from anvesha.db.models import Finding, Source
from anvesha.events import clip
from anvesha.llm.errors import BudgetExceededError, LLMError, LLMInvalidResponseError
from anvesha.llm.types import Message
from anvesha.tools.base import untrusted
from anvesha.verify.quotes import context_around, match_quote

BATCH_SIZE = 8
MAX_PARALLEL_BATCHES = 3
CONTEXT_CHARS = 500

logger = logging.getLogger(__name__)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    item: int = Field(description="The item number.")
    supports: bool = Field(description="Whether the quote, in context, supports the statement.")
    note: str = Field(default="", max_length=1000, description="Short reason, required if false.")


class Verdicts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdicts: list[Verdict] = Field(max_length=50)


SUBMIT_VERDICTS = SubmitTool("submit_verdicts", "Submit one verdict per item.", Verdicts)


@dataclass
class VerificationSummary:
    checked: int = 0
    verified: int = 0
    rejected: int = 0
    not_reviewed: int = 0


async def _set(finding_id: object, ctx: RunContext, status: str, note: str) -> None:
    async with ctx.db.session() as session:
        await session.execute(
            update(Finding).where(Finding.id == finding_id).values(status=status, judge_note=note)
        )
        await session.commit()


async def verify_findings(ctx: RunContext) -> VerificationSummary:
    """Check every finding of the run that hasn't been checked yet."""
    async with ctx.db.session() as session:
        rows = (
            await session.execute(
                select(Finding, Source)
                .join(Source, Source.id == Finding.source_id)
                .where(
                    Finding.run_id == ctx.run_id,
                    Finding.status == "quote_verified",
                    Finding.judge_note.is_(None),
                )
                .order_by(Finding.created_at, Finding.id)
            )
        ).all()
    summary = VerificationSummary(checked=len(rows))
    items: list[tuple[Finding, str]] = []
    for finding, source in rows:
        match = match_quote(finding.quote, source.text)
        if not match.supported:
            await _set(finding.id, ctx, "rejected", "The quote was not found in the source.")
            summary.rejected += 1
            continue
        items.append((finding, context_around(source.text, match.spans[0], width=CONTEXT_CHARS)))

    semaphore = asyncio.Semaphore(MAX_PARALLEL_BATCHES)

    async def judge(batch: list[tuple[Finding, str]]) -> None:
        async with semaphore:
            await _judge_batch(ctx, batch, summary)

    await asyncio.gather(
        *(judge(items[i : i + BATCH_SIZE]) for i in range(0, len(items), BATCH_SIZE))
    )
    return summary


async def _judge_batch(
    ctx: RunContext, batch: list[tuple[Finding, str]], summary: VerificationSummary
) -> None:
    await ctx.check_cancelled()
    rendered = "\n\n".join(
        f"Item {index}\nStatement: {finding.statement}\nQuote: {finding.quote}\nContext: {context}"
        for index, (finding, context) in enumerate(batch, start=1)
    )
    messages = [
        Message.system(prompts.VERIFIER.format(untrusted_rule=prompts.UNTRUSTED_RULE)),
        Message.user(
            f"Check these {len(batch)} items. Statements were written by a research agent; "
            "quotes and context come from outside sources.\n"
            + untrusted("findings_to_verify", rendered)
        ),
    ]
    reason: str | None = None
    verdicts: dict[int, Verdict] = {}
    try:
        result = await structured_call(
            ctx, role="verifier", messages=messages, tool=SUBMIT_VERDICTS, max_tokens=2000
        )
        verdicts = {v.item: v for v in result.verdicts if 1 <= v.item <= len(batch)}
    except BudgetExceededError:
        reason = "budget reached"
    except LLMError as exc:
        if not (exc.retryable or isinstance(exc, LLMInvalidResponseError)):
            raise
        reason = exc.code
        logger.warning("Verifier batch failed in run %s: %s", ctx.run_id, exc)

    for index, (finding, _context) in enumerate(batch, start=1):
        verdict = verdicts.get(index)
        if verdict is None:
            # Kept as quote-verified evidence, labelled as not reviewed by the judge.
            await _set(
                finding.id, ctx, "quote_verified", f"Not reviewed ({reason or 'no verdict'})."
            )
            summary.not_reviewed += 1
        elif verdict.supports:
            await _set(finding.id, ctx, "verified", verdict.note or "Supported.")
            summary.verified += 1
        else:
            await _set(finding.id, ctx, "rejected", verdict.note or "Not supported by the quote.")
            summary.rejected += 1
            await ctx.emit(
                "finding_rejected",
                {"finding_id": str(finding.id), "reason": clip(verdict.note, 300)},
                agent="verifier",
            )
