"""Executes research runs: claims a run, drives the graph, and records the outcome.

Called by the worker for every job (first start, resume after plan approval, and
retries). The graph's checkpoints decide where to continue, so a crashed or retried
run picks up after its last completed step instead of starting over.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from psycopg import OperationalError as PsycopgOperationalError
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, OperationalError

from anvesha.agents.context import PROFILES, RESEARCH_BUDGET_FRACTION, RunCancelled, RunContext
from anvesha.db.models import Run, UsageEvent
from anvesha.errors import AppError
from anvesha.events import add_event, clip
from anvesha.graph.research import build_research_graph
from anvesha.llm.budget import Budget
from anvesha.services import Services
from anvesha.tools import ToolContext

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30.0
RECURSION_LIMIT = 200


class RetryRun(Exception):
    """A temporary failure: the job queue should run this run again later."""


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, AppError):
        return exc.retryable
    if isinstance(exc, OperationalError | PsycopgOperationalError):
        return True  # database connection trouble
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


class RunExecutor:
    def __init__(self, services: Services, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self.services = services
        self.graph = build_research_graph(checkpointer)

    async def execute(self, run_id: uuid.UUID, *, final_attempt: bool = True) -> None:
        run = await self._claim(run_id)
        if run is None:
            return
        settings = self.services.settings
        config: RunnableConfig = {
            "configurable": {"thread_id": str(run.id)},
            "max_concurrency": settings.run_max_parallel_researchers,
            "recursion_limit": RECURSION_LIMIT,
        }
        snapshot = await self.graph.aget_state(config)
        # The graph finished earlier, but the worker stopped before recording it.
        already_finished = bool(snapshot.values) and not snapshot.next and not snapshot.interrupts
        graph_input: Any = None  # None continues from the last checkpoint
        if snapshot.interrupts:
            if run.pending_decision is None:
                await self._await_approval(run)  # nothing to resume with yet
                return
            graph_input = Command(resume=run.pending_decision)
        elif not snapshot.values and not snapshot.next:
            graph_input = {}  # first start

        heartbeat = asyncio.create_task(self._heartbeat(run.id))
        try:
            if not already_finished:
                context = await self._context(run)
                async with asyncio.timeout(settings.run_timeout_minutes * 60):
                    await self.graph.ainvoke(
                        graph_input, config, context=context, durability="sync"
                    )
            snapshot = await self.graph.aget_state(config)
        except RunCancelled:
            await self._finish(run.id, "cancelled", "run_cancelled", {})
            return
        except TimeoutError:
            await self._fail(
                run.id,
                "run_timeout",
                f"The run took longer than {settings.run_timeout_minutes} minutes. Retry "
                "it to continue from where it stopped.",
            )
            return
        except Exception as exc:
            if is_retryable(exc) and not final_attempt:
                logger.warning("Run %s hit a temporary failure; will retry: %s", run.id, exc)
                await self._requeue(run.id, exc)
                raise RetryRun(str(exc)) from exc
            if isinstance(exc, AppError):
                logger.warning("Run %s failed: %s", run.id, exc)
                await self._fail(run.id, exc.code, str(exc))
            else:
                logger.exception("Run %s failed unexpectedly", run.id)
                await self._fail(run.id, "internal_error", "The run failed unexpectedly.")
            return
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        if snapshot.interrupts:
            await self._await_approval(run)
            return
        values = snapshot.values
        await self._finish(
            run.id,
            "completed",
            "run_completed",
            {
                "report_id": values.get("report_id"),
                "report_status": values.get("report_status"),
                "stop_reason": values.get("stop_reason"),
            },
        )

    async def _claim(self, run_id: uuid.UUID) -> Run | None:
        """Mark the run as running, unless there is nothing (left) to do for it."""
        db = self.services.db
        async with db.session() as session:
            run = await session.get(Run, run_id, with_for_update=True)
            if run is None or run.status in ("completed", "failed", "cancelled"):
                return None
            if run.cancel_requested:
                await session.rollback()
                await self._finish(run_id, "cancelled", "run_cancelled", {})
                return None
            if run.status == "awaiting_approval":
                return None  # a stray job; the run continues once the plan is approved
            run.status = "running"
            run.attempts += 1
            run.started_at = run.started_at or datetime.now(UTC)
            run.heartbeat_at = datetime.now(UTC)
            run.error_code = run.error_message = None
            await add_event(
                session,
                run_id=run.id,
                workspace_id=run.workspace_id,
                type="run_started",
                data={"attempt": run.attempts, "resuming": run.pending_decision is not None},
            )
            await session.commit()
            return run

    async def _context(self, run: Run) -> RunContext:
        services = self.services
        async with services.db.session() as session:
            spent, tokens = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                        func.coalesce(
                            func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0
                        ),
                    ).where(UsageEvent.run_id == run.id)
                )
            ).one()
        # Rebuilt from recorded usage, so retries and resumes share one budget.
        budget = Budget(
            max_cost_usd=run.max_cost_usd,
            max_tokens=run.max_tokens,
            spent_usd=Decimal(spent),
            used_tokens=int(tokens),
            usable_fraction=RESEARCH_BUDGET_FRACTION,
        )
        return RunContext(
            run_id=run.id,
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            question=run.question,
            depth=run.depth,
            autopilot=run.autopilot,
            profile=PROFILES[run.depth],
            budget=budget,
            db=services.db,
            settings=services.settings,
            gateway=services.gateway,
            sources=services.sources,
            tools=services.tools,
            tool_context=ToolContext(
                workspace_id=run.workspace_id,
                project_id=run.project_id,
                db=services.db,
                settings=services.settings,
                http=services.http,
                vault=services.vault,
                gateway=services.gateway,
                sources=services.sources,
                added_by="agent",
            ),
        )

    async def _heartbeat(self, run_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with self.services.db.session() as session:
                    await session.execute(
                        update(Run).where(Run.id == run_id).values(heartbeat_at=func.now())
                    )
                    await session.commit()
            except Exception:
                logger.warning("Heartbeat failed for run %s", run_id, exc_info=True)

    async def _transition(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        event: str,
        data: dict[str, Any],
        values: dict[str, Any] | None = None,
    ) -> None:
        """Change the run's status and record the matching event in one transaction.

        Committing both together is what lets event streams end exactly after the last
        event (see anvesha.events.stream).
        """
        async with self.services.db.session() as session:
            run = await session.get(Run, run_id, with_for_update=True)
            if run is None or run.status in ("completed", "failed", "cancelled"):
                return
            run.status = status
            for key, value in (values or {}).items():
                setattr(run, key, value)
            await add_event(
                session, run_id=run.id, workspace_id=run.workspace_id, type=event, data=data
            )
            await session.commit()

    async def _finish(
        self, run_id: uuid.UUID, status: str, event: str, data: dict[str, Any]
    ) -> None:
        await self._transition(
            run_id,
            status=status,
            event=event,
            data=data,
            values={"finished_at": datetime.now(UTC), "pending_decision": None},
        )

    async def _fail(self, run_id: uuid.UUID, code: str, message: str) -> None:
        await self._transition(
            run_id,
            status="failed",
            event="run_failed",
            data={"code": code, "message": clip(message, 1000)},
            values={
                "finished_at": datetime.now(UTC),
                "error_code": code[:64],
                "error_message": message[:4000],
            },
        )

    async def _requeue(self, run_id: uuid.UUID, exc: BaseException) -> None:
        code = exc.code if isinstance(exc, AppError) else "temporary_failure"
        await self._transition(
            run_id,
            status="queued",
            event="run_retrying",
            data={"code": code, "message": clip(str(exc), 500)},
        )

    async def _await_approval(self, run: Run) -> None:
        async with self.services.db.session() as session:
            plan = await session.scalar(select(Run.plan).where(Run.id == run.id))
        await self._transition(
            run.id,
            status="awaiting_approval",
            event="plan_ready",
            data={"plan": plan or {}},
            values={"pending_decision": None},
        )
