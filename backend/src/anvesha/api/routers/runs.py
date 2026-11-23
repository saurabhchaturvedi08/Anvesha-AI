import uuid
from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from anvesha import runs as run_service
from anvesha.api.deps import (
    BrokerDep,
    DatabaseDep,
    PrincipalDep,
    QueueDep,
    SessionDep,
    SettingsDep,
    VaultDep,
)
from anvesha.api.schemas.runs import (
    FindingRead,
    PlanApproval,
    ReportRead,
    ReportSummary,
    RunCreate,
    RunDetail,
    RunEventRead,
    RunRead,
    RunUsage,
    SubQuestionRead,
)
from anvesha.db.models import Finding, Report, Run, RunEvent, SubQuestion, UsageEvent
from anvesha.errors import NotFoundError
from anvesha.events.stream import stream_run_events
from anvesha.sources.service import require_project

router = APIRouter(prefix="/api/v1", tags=["runs"])

FindingStatus = Literal["quote_verified", "verified", "rejected"]


@router.post(
    "/projects/{project_id}/runs", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED
)
async def start_run(
    project_id: uuid.UUID,
    payload: RunCreate,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    vault: VaultDep,
    queue: QueueDep,
) -> Run:
    """Start researching the project. Follow progress at ``/runs/{id}/events/stream``.

    Unless ``autopilot`` is set, the run pauses with status ``awaiting_approval`` once
    the lead researcher has a plan; approve it with ``POST /runs/{id}/approve``.
    """
    return await run_service.create_run(
        session,
        settings=settings,
        vault=vault,
        queue=queue,
        principal=principal,
        project_id=project_id,
        question=payload.question,
        depth=payload.depth,
        autopilot=payload.autopilot,
        max_cost_usd=payload.max_cost_usd,
        max_tokens=payload.max_tokens,
    )


@router.get("/projects/{project_id}/runs", response_model=list[RunRead])
async def list_runs(
    project_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Run]:
    await require_project(session, principal.workspace_id, project_id)
    result = await session.scalars(
        select(Run)
        .where(Run.workspace_id == principal.workspace_id, Run.project_id == project_id)
        .order_by(Run.created_at.desc(), Run.id)
        .limit(limit)
        .offset(offset)
    )
    return result.all()


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, session: SessionDep, principal: PrincipalDep) -> RunDetail:
    run = await run_service.get_run(session, principal, run_id)
    sub_questions = (
        await session.scalars(
            select(SubQuestion)
            .where(SubQuestion.run_id == run.id)
            .order_by(SubQuestion.round, SubQuestion.ordinal)
        )
    ).all()
    calls, input_tokens, output_tokens, cost, unpriced = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                func.count().filter(UsageEvent.cost_usd.is_(None), UsageEvent.status == "ok"),
            ).where(UsageEvent.run_id == run.id)
        )
    ).one()
    counts = dict(
        (
            await session.execute(
                select(Finding.status, func.count())
                .where(Finding.run_id == run.id)
                .group_by(Finding.status)
            )
        ).all()
    )
    report_id = await session.scalar(select(Report.id).where(Report.run_id == run.id))
    return RunDetail(
        **RunRead.model_validate(run).model_dump(),
        sub_questions=[SubQuestionRead.model_validate(sq) for sq in sub_questions],
        usage=RunUsage(
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            unpriced_calls=unpriced,
        ),
        findings=counts,
        report_id=report_id,
    )


@router.post("/runs/{run_id}/approve", response_model=RunRead, status_code=202)
async def approve_plan(
    run_id: uuid.UUID,
    payload: PlanApproval,
    session: SessionDep,
    principal: PrincipalDep,
    queue: QueueDep,
) -> Run:
    """Approve the plan (optionally with edited sub-questions) and continue the run."""
    return await run_service.approve_plan(
        session,
        queue=queue,
        principal=principal,
        run_id=run_id,
        sub_questions=(
            [item.model_dump() for item in payload.sub_questions]
            if payload.sub_questions is not None
            else None
        ),
        strategy=payload.strategy,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunRead, status_code=202)
async def cancel_run(run_id: uuid.UUID, session: SessionDep, principal: PrincipalDep) -> Run:
    """Cancel the run. A running run stops at its next checkpoint (within seconds)."""
    return await run_service.cancel_run(session, principal=principal, run_id=run_id)


@router.post("/runs/{run_id}/retry", response_model=RunRead, status_code=202)
async def retry_run(
    run_id: uuid.UUID, session: SessionDep, principal: PrincipalDep, queue: QueueDep
) -> Run:
    """Continue a failed run from its last completed step."""
    return await run_service.retry_run(session, queue=queue, principal=principal, run_id=run_id)


@router.get("/runs/{run_id}/events", response_model=list[RunEventRead])
async def list_events(
    run_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    after: Annotated[int, Query(ge=0, description="Only events with a larger id.")] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> Sequence[RunEvent]:
    run = await run_service.get_run(session, principal, run_id)
    result = await session.scalars(
        select(RunEvent)
        .where(RunEvent.run_id == run.id, RunEvent.id > after)
        .order_by(RunEvent.id)
        .limit(limit)
    )
    return result.all()


@router.get(
    "/runs/{run_id}/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_events(
    run_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    db: DatabaseDep,
    broker: BrokerDep,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Live activity as Server-Sent Events.

    Replays past events first (after ``after``, or the ``Last-Event-ID`` header browsers
    send when reconnecting), then streams new ones. Ends with an ``end`` event when the
    run completes, fails, or is cancelled.
    """
    run = await run_service.get_run(session, principal, run_id)
    await session.close()  # don't hold a connection for the life of the stream
    start_after = after
    if last_event_id and last_event_id.strip().isdigit():
        start_after = max(start_after, int(last_event_id.strip()))
    return StreamingResponse(
        stream_run_events(
            db, broker, run.id, after_id=start_after, is_disconnected=request.is_disconnected
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/findings", response_model=list[FindingRead])
async def list_findings(
    run_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    status_filter: Annotated[FindingStatus | None, Query(alias="status")] = None,
) -> Sequence[Finding]:
    run = await run_service.get_run(session, principal, run_id)
    query = select(Finding).where(Finding.run_id == run.id)
    if status_filter is not None:
        query = query.where(Finding.status == status_filter)
    return (await session.scalars(query.order_by(Finding.created_at, Finding.id))).all()


@router.get("/runs/{run_id}/report", response_model=ReportRead)
async def get_run_report(run_id: uuid.UUID, session: SessionDep, principal: PrincipalDep) -> Report:
    run = await run_service.get_run(session, principal, run_id)
    report = await session.scalar(select(Report).where(Report.run_id == run.id))
    if report is None:
        raise NotFoundError("This run has no report yet.")
    return report


@router.get("/projects/{project_id}/reports", response_model=list[ReportSummary])
async def list_reports(
    project_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Sequence[Report]:
    await require_project(session, principal.workspace_id, project_id)
    result = await session.scalars(
        select(Report)
        .where(Report.workspace_id == principal.workspace_id, Report.project_id == project_id)
        .order_by(Report.version.desc())
    )
    return result.all()


@router.get("/reports/{report_id}", response_model=ReportRead)
async def get_report(report_id: uuid.UUID, session: SessionDep, principal: PrincipalDep) -> Report:
    report = await session.scalar(
        select(Report).where(Report.id == report_id, Report.workspace_id == principal.workspace_id)
    )
    if report is None:
        raise NotFoundError("Report not found")
    return report
