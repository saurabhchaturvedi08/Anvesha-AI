"""Starting and steering research runs (used by the API, and later the MCP server).

State changes here are the user's side of a run: create, approve the plan, cancel,
retry. The worker owns everything in between (see anvesha.graph.executor). Each change
is committed together with its event, then the run is handed to the job queue.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.agents.context import PROFILES
from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.models import Run
from anvesha.errors import AppError, InvalidInputError, NotFoundError
from anvesha.events import add_event
from anvesha.llm.credentials import resolve_route
from anvesha.llm.errors import ModelNotConfiguredError
from anvesha.llm.types import AGENT_ROLES
from anvesha.sources.service import require_project
from anvesha.tenancy import Principal
from anvesha.worker.queue import JobQueue

logger = logging.getLogger(__name__)

TERMINAL = ("completed", "failed", "cancelled")


class RunStateError(AppError):
    code = "invalid_run_state"
    http_status = 409


class RunInProgressError(AppError):
    code = "run_in_progress"
    http_status = 409


async def get_run(
    session: AsyncSession, principal: Principal, run_id: uuid.UUID, *, lock: bool = False
) -> Run:
    query = select(Run).where(Run.id == run_id, Run.workspace_id == principal.workspace_id)
    if lock:
        query = query.with_for_update()
    run = await session.scalar(query)
    if run is None:
        raise NotFoundError("Run not found")
    return run


async def check_models(
    session: AsyncSession, settings: Settings, vault: SecretBox | None, workspace_id: uuid.UUID
) -> None:
    """Fail before queueing if any agent role has no usable model (one message for all)."""
    problems: list[str] = []
    for role in AGENT_ROLES:
        try:
            await resolve_route(session, settings, vault, workspace_id, role)
        except ModelNotConfiguredError as exc:
            problems.append(str(exc))
    if problems:
        raise ModelNotConfiguredError(" ".join(problems))


def resolve_cost_limit(settings: Settings, requested: Decimal | None) -> Decimal | None:
    limit = requested if requested is not None else settings.run_default_max_cost_usd
    ceiling = settings.run_max_cost_ceiling_usd
    if limit is not None and ceiling is not None and limit > ceiling:
        raise InvalidInputError(
            f"max_cost_usd can be at most {ceiling} on this server.", code="budget_too_high"
        )
    return limit


async def _enqueue(queue: JobQueue, run_id: uuid.UUID) -> None:
    # If this fails the run stays queued, and the worker's recovery task picks it up
    # within a few minutes, so the request itself still succeeds.
    try:
        await queue.enqueue_run(run_id)
    except Exception:
        logger.exception("Could not queue run %s; recovery will retry", run_id)


async def create_run(
    session: AsyncSession,
    *,
    settings: Settings,
    vault: SecretBox | None,
    queue: JobQueue,
    principal: Principal,
    project_id: uuid.UUID,
    question: str | None,
    depth: str | None,
    autopilot: bool,
    max_cost_usd: Decimal | None,
    max_tokens: int | None,
) -> Run:
    project = await require_project(session, principal.workspace_id, project_id)
    await check_models(session, settings, vault, principal.workspace_id)
    run = Run(
        workspace_id=principal.workspace_id,
        project_id=project.id,
        created_by_id=principal.user_id,
        question=question or project.objective,
        depth=depth or project.depth,
        autopilot=autopilot,
        status="queued",
        max_cost_usd=resolve_cost_limit(settings, max_cost_usd),
        max_tokens=max_tokens if max_tokens is not None else settings.run_default_max_tokens,
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RunInProgressError(
            "This project already has a run in progress. Wait for it, or cancel it first."
        ) from exc
    await add_event(
        session,
        run_id=run.id,
        workspace_id=run.workspace_id,
        type="run_queued",
        data={
            "depth": run.depth,
            "autopilot": run.autopilot,
            "max_cost_usd": str(run.max_cost_usd) if run.max_cost_usd else None,
        },
    )
    await session.commit()
    await _enqueue(queue, run.id)
    return run


async def approve_plan(
    session: AsyncSession,
    *,
    queue: JobQueue,
    principal: Principal,
    run_id: uuid.UUID,
    sub_questions: list[dict[str, str]] | None,
    strategy: str | None,
) -> Run:
    run = await get_run(session, principal, run_id, lock=True)
    if run.status != "awaiting_approval":
        raise RunStateError(f"The run is {run.status}, not waiting for plan approval.")
    if (
        sub_questions is not None
        and len(sub_questions) > max(PROFILES[run.depth].max_sub_questions, 1) * 2
    ):
        raise InvalidInputError("Too many sub-questions for this run's depth.")
    decision: dict[str, object] = {}
    if sub_questions is not None:
        decision["sub_questions"] = sub_questions
        decision["strategy"] = (
            strategy if strategy is not None else (run.plan or {}).get("strategy", "")
        )
    run.pending_decision = decision
    run.status = "queued"
    await add_event(
        session,
        run_id=run.id,
        workspace_id=run.workspace_id,
        type="plan_approval_received",
        data={"edited": sub_questions is not None},
        agent="user",
    )
    await session.commit()
    await _enqueue(queue, run.id)
    return run


async def cancel_run(session: AsyncSession, *, principal: Principal, run_id: uuid.UUID) -> Run:
    run = await get_run(session, principal, run_id, lock=True)
    if run.status in TERMINAL:
        raise RunStateError(f"The run is already {run.status}.")
    run.cancel_requested = True
    data: dict[str, object] = {}
    if run.status == "running":
        # The worker stops at its next checkpoint and records the cancellation.
        event = "cancel_requested"
    else:
        # Not executing right now: cancel immediately. A queued job finds it cancelled.
        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
        run.pending_decision = None
        event = "run_cancelled"
    await add_event(
        session, run_id=run.id, workspace_id=run.workspace_id, type=event, data=data, agent="user"
    )
    await session.commit()
    return run


async def retry_run(
    session: AsyncSession, *, queue: JobQueue, principal: Principal, run_id: uuid.UUID
) -> Run:
    """Continue a failed run from its last checkpoint (e.g. after fixing a key)."""
    run = await get_run(session, principal, run_id, lock=True)
    if run.status != "failed":
        raise RunStateError(f"Only failed runs can be retried; this one is {run.status}.")
    run.status = "queued"
    run.error_code = run.error_message = None
    run.finished_at = None
    run.cancel_requested = False
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RunInProgressError(
            "This project has another run in progress. Wait for it, or cancel it first."
        ) from exc
    await add_event(
        session,
        run_id=run.id,
        workspace_id=run.workspace_id,
        type="run_queued",
        data={"retry": True},
        agent="user",
    )
    await session.commit()
    await _enqueue(queue, run.id)
    return run
