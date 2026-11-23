"""What every agent in a run shares: identity, limits, budget, services, and progress events."""

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from anvesha.config import Settings
from anvesha.db.models import Run
from anvesha.db.session import Database
from anvesha.events import emit
from anvesha.llm.budget import Budget
from anvesha.llm.gateway import LLMGateway
from anvesha.llm.types import AgentRole, Completion, Message, ToolSpec
from anvesha.sources.service import SourceService
from anvesha.tools import ToolContext, ToolRegistry


class RunCancelled(Exception):
    """The user cancelled the run; agents stop at their next checkpoint."""


@dataclass(frozen=True)
class DepthProfile:
    """How much work a run may do. Chosen by the project's (or run's) depth."""

    max_rounds: int  # research rounds: the initial plan plus follow-ups for gaps
    max_sub_questions: int  # in the initial plan
    max_follow_ups: int  # new sub-questions per follow-up round
    researcher_max_steps: int  # model calls per researcher
    max_findings_per_researcher: int


PROFILES: dict[str, DepthProfile] = {
    "quick": DepthProfile(1, 3, 0, 8, 6),
    "standard": DepthProfile(2, 5, 3, 14, 8),
    "deep": DepthProfile(3, 7, 4, 22, 12),
}
# Research may spend this share of the budget; the rest is kept for writing the report.
RESEARCH_BUDGET_FRACTION = Decimal("0.85")
# How often agents check the database for a cancellation request.
CANCEL_CHECK_SECONDS = 2.0


@dataclass
class RunContext:
    """Passed to the graph as LangGraph runtime context (never stored in checkpoints)."""

    run_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    question: str
    depth: str
    autopilot: bool
    profile: DepthProfile
    budget: Budget
    db: Database
    settings: Settings
    gateway: LLMGateway
    sources: SourceService
    tools: ToolRegistry
    tool_context: ToolContext
    _cancel_checked_at: float = field(default=0.0, repr=False)

    async def emit(
        self, type: str, data: dict[str, Any] | None = None, *, agent: str | None = None
    ) -> None:
        await emit(
            self.db,
            run_id=self.run_id,
            workspace_id=self.workspace_id,
            type=type,
            data=data,
            agent=agent,
        )

    async def check_cancelled(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._cancel_checked_at < CANCEL_CHECK_SECONDS:
            return
        self._cancel_checked_at = now
        async with self.db.session() as session:
            requested = await session.scalar(
                select(Run.cancel_requested).where(Run.id == self.run_id)
            )
        if requested is None or requested:
            raise RunCancelled()

    async def complete(
        self,
        role: AgentRole,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int | None = None,
    ) -> Completion:
        return await self.gateway.complete(
            workspace_id=self.workspace_id,
            role=role,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            budget=self.budget,
            project_id=self.project_id,
            run_id=self.run_id,
        )
