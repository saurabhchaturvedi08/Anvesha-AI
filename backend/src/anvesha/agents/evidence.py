"""Loading a run's sub-questions and findings for the agents that reason over them."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from anvesha.agents.context import RunContext
from anvesha.db.models import Finding, Source, SubQuestion

# Findings the writer may cite: the quote is in the source, and the verifier either
# agreed it supports the statement or couldn't review it.
CITABLE_STATUSES = ("verified", "quote_verified")


@dataclass(frozen=True)
class FindingView:
    id: uuid.UUID
    sub_question_id: uuid.UUID | None
    statement: str
    quote: str
    status: str
    judge_note: str | None
    page: int | None
    source_id: uuid.UUID
    source_title: str | None
    source_url: str | None
    source_filename: str | None
    published: str | None
    site_name: str | None


async def load_sub_questions(ctx: RunContext) -> list[SubQuestion]:
    async with ctx.db.session() as session:
        rows = await session.scalars(
            select(SubQuestion)
            .where(SubQuestion.run_id == ctx.run_id)
            .order_by(SubQuestion.round, SubQuestion.ordinal)
        )
        return list(rows.all())


async def load_findings(
    ctx: RunContext, statuses: Sequence[str] = CITABLE_STATUSES
) -> list[FindingView]:
    """Findings in a stable order: by sub-question (plan order), then as recorded."""
    async with ctx.db.session() as session:
        rows = (
            await session.execute(
                select(Finding, Source)
                .join(Source, Source.id == Finding.source_id)
                .outerjoin(SubQuestion, SubQuestion.id == Finding.sub_question_id)
                .where(Finding.run_id == ctx.run_id, Finding.status.in_(statuses))
                .order_by(
                    SubQuestion.round.nulls_last(),
                    SubQuestion.ordinal.nulls_last(),
                    Finding.created_at,
                    Finding.id,
                )
            )
        ).all()
    return [
        FindingView(
            id=finding.id,
            sub_question_id=finding.sub_question_id,
            statement=finding.statement,
            quote=finding.quote,
            status=finding.status,
            judge_note=finding.judge_note,
            page=finding.page,
            source_id=source.id,
            source_title=source.title,
            source_url=source.url,
            source_filename=source.filename,
            published=source.published,
            site_name=source.site_name,
        )
        for finding, source in rows
    ]


def source_label(finding: FindingView) -> str:
    return finding.source_title or finding.source_url or finding.source_filename or "untitled"
