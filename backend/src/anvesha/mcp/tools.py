"""The tools Anvesha offers MCP clients.

Two kinds:

* Research tools run Anvesha's own agents and read what they produced: start a run,
  follow and steer it, read the report, search the verified evidence.
* Raw tools are the research agents' own tools (web and scholarly search, fetching and
  reading sources, quote verification), so the client's model can do the research itself
  and still get quotes checked in code. They act on one project, named by ``project_id``.

Every tool returns text for the model plus the same facts as structured data. Failures
come back as error results, never as exceptions, so the client's model can adapt.
"""

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY, distinct_on

from anvesha import runs as run_service
from anvesha.api.schemas.runs import PlanEdit
from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.models import (
    Finding,
    Project,
    Report,
    Run,
    RunEvent,
    Source,
    SubQuestion,
    UsageEvent,
)
from anvesha.db.models.sources import FTS_CONFIG
from anvesha.db.session import Database
from anvesha.errors import AppError, InvalidInputError, NotFoundError
from anvesha.events import clip
from anvesha.llm.gateway import LLMGateway
from anvesha.sources.service import SourceService, require_project
from anvesha.tenancy import Principal
from anvesha.tools import Tool, ToolContext, ToolRegistry, ToolResult
from anvesha.tools.base import invalid_arguments, tool_parameters_schema, untrusted
from anvesha.worker.queue import JobQueue

Depth = Literal["quick", "standard", "deep"]
MAX_EVENTS = 50


@dataclass(frozen=True)
class McpServices:
    """The process-wide services MCP tools use (the API process's, shared)."""

    settings: Settings
    db: Database
    vault: SecretBox | None
    http: httpx2.AsyncClient
    gateway: LLMGateway
    sources: SourceService
    tools: ToolRegistry
    queue: JobQueue


@dataclass(frozen=True)
class McpContext:
    principal: Principal
    services: McpServices


@dataclass(frozen=True)
class McpTool:
    name: str
    title: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[McpContext, Any], Awaitable[ToolResult]]
    read_only: bool = False
    destructive: bool = False
    # Reaches outside Anvesha (the web, scholarly APIs, model providers).
    open_world: bool = False
    # Overrides the schema derived from args_model (raw tools add project_id).
    schema: dict[str, Any] | None = None

    def input_schema(self) -> dict[str, Any]:
        return self.schema or tool_parameters_schema(self.args_model)

    async def invoke(self, ctx: McpContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            args = self.args_model.model_validate(arguments)
        except ValidationError as exc:
            return invalid_arguments(exc)
        try:
            return await self.handler(ctx, args)
        except AppError as exc:
            return ToolResult.error(str(exc), exc.code, retryable=exc.retryable)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Projects ---------------------------------------------------------------------------


class ListProjectsArgs(_Args):
    limit: int = Field(default=20, ge=1, le=100)


async def _list_projects(ctx: McpContext, args: ListProjectsArgs) -> ToolResult:
    async with ctx.services.db.session() as session:
        projects = (
            await session.scalars(
                select(Project)
                .where(Project.workspace_id == ctx.principal.workspace_id)
                .order_by(Project.created_at.desc(), Project.id)
                .limit(args.limit)
            )
        ).all()
        latest: dict[uuid.UUID, Run] = {}
        if projects:
            newest_runs = await session.scalars(
                select(Run)
                .where(Run.project_id.in_([p.id for p in projects]))
                .ext(distinct_on(Run.project_id))
                .order_by(Run.project_id, Run.created_at.desc())
            )
            latest = {newest.project_id: newest for newest in newest_runs}
    if not projects:
        return ToolResult(
            "No projects yet. Start one with start_research or create_project.", {"projects": []}
        )
    items = []
    lines = []
    for project in projects:
        run = latest.get(project.id)
        items.append(
            {
                "project_id": str(project.id),
                "title": project.title,
                "objective": project.objective,
                "depth": project.depth,
                "latest_run": {"run_id": str(run.id), "status": run.status} if run else None,
            }
        )
        status = f"latest run {run.id} is {run.status}" if run else "no runs yet"
        lines.append(f"- {project.id}: {project.title} ({project.depth}; {status})")
    return ToolResult(
        f"{len(projects)} project(s), newest first:\n" + "\n".join(lines), {"projects": items}
    )


class CreateProjectArgs(_Args):
    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(
        min_length=1, max_length=10_000, description="The research question, in full."
    )
    depth: Depth = "standard"


async def _create_project(ctx: McpContext, args: CreateProjectArgs) -> ToolResult:
    async with ctx.services.db.session() as session:
        project = Project(
            workspace_id=ctx.principal.workspace_id,
            created_by_id=ctx.principal.user_id,
            title=args.title,
            objective=args.objective,
            depth=args.depth,
        )
        session.add(project)
        await session.commit()
    return ToolResult(
        f"Created project {project.id}: {project.title}. Use this project_id with the search, "
        "fetch and verify tools, or call start_research to have Anvesha's agents research it.",
        {"project_id": str(project.id), "title": project.title, "depth": project.depth},
    )


# --- Research runs ----------------------------------------------------------------------


class StartResearchArgs(_Args):
    question: str = Field(min_length=1, max_length=10_000, description="What to research.")
    project_id: uuid.UUID | None = Field(
        default=None, description="Research in this existing project. Empty: create a new one."
    )
    title: str | None = Field(
        default=None, max_length=255, description="Title for a new project (optional)."
    )
    depth: Depth | None = Field(
        default=None,
        description="quick (a few minutes), standard, or deep (most thorough, slowest). "
        "Defaults to the project's depth (standard for a new project).",
    )
    autopilot: bool = Field(
        default=True,
        description="Run straight through. False: pause after planning so the user can "
        "review the plan (see approve_plan).",
    )
    max_cost_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=10,
        decimal_places=4,
        description="Spending limit for model calls. Empty: the server's default.",
    )


async def _start_research(ctx: McpContext, args: StartResearchArgs) -> ToolResult:
    services = ctx.services
    async with services.db.session() as session:
        project_id = args.project_id
        if project_id is None:
            # Check first, so a missing model doesn't leave an empty project behind.
            await run_service.check_models(
                session, services.settings, services.vault, ctx.principal.workspace_id
            )
            project = Project(
                workspace_id=ctx.principal.workspace_id,
                created_by_id=ctx.principal.user_id,
                title=args.title or clip(args.question, 120),
                objective=args.question,
                depth=args.depth or "standard",
            )
            session.add(project)
            await session.flush()
            project_id = project.id
        # Commits the new project together with the run.
        run = await run_service.create_run(
            session,
            settings=services.settings,
            vault=services.vault,
            queue=services.queue,
            principal=ctx.principal,
            project_id=project_id,
            question=args.question,
            depth=args.depth,
            autopilot=args.autopilot,
            max_cost_usd=args.max_cost_usd,
            max_tokens=None,
        )
    limit = f" with a ${run.max_cost_usd} spending limit" if run.max_cost_usd else ""
    if run.autopilot:
        next_step = (
            "Research takes from a few minutes (quick) to half an hour or more (deep). Check "
            "on it with get_research_status, then read the result with get_report."
        )
    else:
        next_step = (
            "The lead researcher is writing a plan. Poll get_research_status until the status "
            "is awaiting_approval, show the plan to the user, then call approve_plan."
        )
    return ToolResult(
        f"Started research run {run.id} in project {project_id} ({run.depth} depth{limit}). "
        + next_step,
        {
            "run_id": str(run.id),
            "project_id": str(project_id),
            "status": run.status,
            "depth": run.depth,
            "autopilot": run.autopilot,
            "max_cost_usd": str(run.max_cost_usd) if run.max_cost_usd else None,
        },
    )


class RunArgs(_Args):
    run_id: uuid.UUID


class ResearchStatusArgs(RunArgs):
    recent_events: int = Field(
        default=8, ge=0, le=MAX_EVENTS, description="How many recent activity entries to include."
    )


_NEXT_STEP = {
    "queued": "Waiting for a worker to pick it up. Check again shortly.",
    "running": "Still researching. Check again in a minute or two.",
    "awaiting_approval": "The plan is ready for review. Show it to the user, then call "
    "approve_plan (with edited sub-questions if they want changes) or cancel_research.",
    "completed": "Done. Read the result with get_report.",
    "failed": "The run failed (see the error). It can be retried from the Anvesha app.",
    "cancelled": "The run was cancelled.",
}


async def _research_status(ctx: McpContext, args: ResearchStatusArgs) -> ToolResult:
    async with ctx.services.db.session() as session:
        run = await run_service.get_run(session, ctx.principal, args.run_id)
        sub_questions = (
            await session.scalars(
                select(SubQuestion)
                .where(SubQuestion.run_id == run.id)
                .order_by(SubQuestion.round, SubQuestion.ordinal)
            )
        ).all()
        counts = dict(
            (
                await session.execute(
                    select(Finding.status, func.count())
                    .where(Finding.run_id == run.id)
                    .group_by(Finding.status)
                )
            ).all()
        )
        cost = await session.scalar(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
                UsageEvent.run_id == run.id
            )
        )
        report_id = await session.scalar(select(Report.id).where(Report.run_id == run.id))
        events: Sequence[RunEvent] = []
        if args.recent_events:
            events = (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run.id)
                    .order_by(RunEvent.id.desc())
                    .limit(args.recent_events)
                )
            ).all()

    lines = [
        f"Run {run.id}: {run.status}",
        f"Question: {run.question}",
        f"Depth: {run.depth}. Spent so far: ${Decimal(cost or 0):.4f}"
        + (f" of ${run.max_cost_usd}" if run.max_cost_usd else ""),
    ]
    if run.error_message:
        lines.append(f"Error ({run.error_code}): {run.error_message}")
    plan = run.plan or {}
    if run.status == "awaiting_approval" and plan:
        lines.append(f"Proposed strategy: {plan.get('strategy') or '(none)'}")
        lines.append("Proposed sub-questions:")
        for number, item in enumerate(plan.get("sub_questions") or [], start=1):
            rationale = f" (why: {item.get('rationale')})" if item.get("rationale") else ""
            lines.append(f"  {number}. {item.get('question')}{rationale}")
    elif sub_questions:
        lines.append("Sub-questions:")
        for sq in sub_questions:
            coverage = f", {sq.coverage}" if sq.coverage else ""
            lines.append(f"  - [{sq.status}{coverage}] {sq.question}")
    if counts:
        lines.append(
            "Findings: " + ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        )
    if report_id:
        lines.append(f"Report: {report_id}")
    if events:
        activity = [
            f"{event.created_at:%H:%M:%S} {event.agent or 'system'}: {event.type}"
            + (f" {clip(json.dumps(event.data, default=str), 160)}" if event.data else "")
            for event in reversed(events)
        ]
        lines.append("Recent activity:\n" + untrusted("run_events", "\n".join(activity)))
    lines.append(f"Next: {_NEXT_STEP[run.status]}")
    return ToolResult(
        "\n".join(lines),
        {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
            "status": run.status,
            "question": run.question,
            "depth": run.depth,
            "plan": run.plan,
            "sub_questions": [
                {
                    "id": str(sq.id),
                    "round": sq.round,
                    "question": sq.question,
                    "status": sq.status,
                    "coverage": sq.coverage,
                    "summary": sq.summary,
                }
                for sq in sub_questions
            ],
            "findings": counts,
            "cost_usd": str(cost or 0),
            "max_cost_usd": str(run.max_cost_usd) if run.max_cost_usd else None,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "report_id": str(report_id) if report_id else None,
        },
    )


class ApprovePlanArgs(RunArgs):
    sub_questions: list[PlanEdit] | None = Field(
        default=None,
        min_length=1,
        max_length=12,
        description="Replacement sub-questions. Leave empty to approve the plan as proposed.",
    )
    strategy: str | None = Field(default=None, max_length=2000)


async def _approve_plan(ctx: McpContext, args: ApprovePlanArgs) -> ToolResult:
    async with ctx.services.db.session() as session:
        run = await run_service.approve_plan(
            session,
            queue=ctx.services.queue,
            principal=ctx.principal,
            run_id=args.run_id,
            sub_questions=(
                [item.model_dump() for item in args.sub_questions]
                if args.sub_questions is not None
                else None
            ),
            strategy=args.strategy,
        )
    edited = " with your edits" if args.sub_questions is not None else ""
    return ToolResult(
        f"Plan approved{edited}. Run {run.id} continues; check on it with get_research_status.",
        {"run_id": str(run.id), "status": run.status},
    )


async def _cancel_research(ctx: McpContext, args: RunArgs) -> ToolResult:
    async with ctx.services.db.session() as session:
        run = await run_service.cancel_run(session, principal=ctx.principal, run_id=args.run_id)
    message = (
        f"Run {run.id} is cancelled."
        if run.status == "cancelled"
        else f"Run {run.id} will stop at its next checkpoint (within seconds)."
    )
    return ToolResult(message, {"run_id": str(run.id), "status": run.status})


# --- Reports and evidence ---------------------------------------------------------------


class GetReportArgs(_Args):
    run_id: uuid.UUID | None = Field(default=None, description="The run whose report to read.")
    project_id: uuid.UUID | None = Field(
        default=None, description="Or: read this project's latest report."
    )
    include_quotes: bool = Field(
        default=False,
        description="Also list the verified quote behind every citation.",
    )


async def _get_report(ctx: McpContext, args: GetReportArgs) -> ToolResult:
    if (args.run_id is None) == (args.project_id is None):
        raise InvalidInputError("Give either run_id or project_id.")
    workspace_id = ctx.principal.workspace_id
    async with ctx.services.db.session() as session:
        query = select(Report).where(Report.workspace_id == workspace_id)
        if args.run_id is not None:
            query = query.where(Report.run_id == args.run_id)
        else:
            query = query.where(Report.project_id == args.project_id).order_by(
                Report.version.desc()
            )
        report = await session.scalar(query.limit(1))
        if report is None:
            if args.run_id is not None:
                run = await run_service.get_run(session, ctx.principal, args.run_id)
                raise NotFoundError(
                    f"Run {run.id} has no report yet (it is {run.status}). "
                    + _NEXT_STEP[run.status]
                )
            await require_project(session, workspace_id, args.project_id)  # type: ignore[arg-type]
            raise NotFoundError("This project has no reports yet.")
        findings: dict[str, Finding] = {}
        if args.include_quotes:
            ids = [fid for c in report.citations for fid in c.get("finding_ids", [])]
            if ids:
                rows = await session.scalars(
                    select(Finding).where(
                        Finding.workspace_id == workspace_id,
                        Finding.id.in_([uuid.UUID(str(i)) for i in ids]),
                    )
                )
                findings = {str(f.id): f for f in rows}

    parts = [
        f"Report {report.id} (version {report.version}, {report.status}) for run {report.run_id}:",
        untrusted(f"report:{report.id}", report.markdown),
    ]
    if report.status == "incomplete":
        parts.insert(1, "This report is incomplete: the run stopped before finishing.")
    if findings:
        quotes = []
        for citation in report.citations:
            for fid in citation.get("finding_ids", []):
                if finding := findings.get(str(fid)):
                    page = f", page {finding.page}" if finding.page else ""
                    quotes.append(f'[{citation.get("number")}] "{finding.quote}"{page}')
        parts.append(
            "Verified quotes behind the citations:\n" + untrusted("quotes", "\n".join(quotes))
        )
    return ToolResult(
        "\n".join(parts),
        {
            "report_id": str(report.id),
            "run_id": str(report.run_id),
            "project_id": str(report.project_id),
            "version": report.version,
            "status": report.status,
            "title": report.title,
            "markdown": report.markdown,
            "citations": report.citations,
        },
    )


class SearchEvidenceArgs(_Args):
    project_id: uuid.UUID
    query: str = Field(
        default="",
        max_length=500,
        description="Words to look for in findings. Empty: the most recent findings.",
    )
    run_id: uuid.UUID | None = Field(default=None, description="Only this run's findings.")
    include_rejected: bool = Field(
        default=False,
        description="Also return findings the verifier rejected (the quote exists, but "
        "doesn't support the statement).",
    )
    limit: int = Field(default=20, ge=1, le=100)


async def _search_evidence(ctx: McpContext, args: SearchEvidenceArgs) -> ToolResult:
    statuses = ["verified", "quote_verified"] + (["rejected"] if args.include_rejected else [])
    workspace_id = ctx.principal.workspace_id
    has_words = bool(re.search(r"\w{2,}", args.query))
    async with ctx.services.db.session() as session:
        await require_project(session, workspace_id, args.project_id)
        query = (
            select(Finding, Source)
            .join(Source, Source.id == Finding.source_id)
            .where(
                Finding.workspace_id == workspace_id,
                Finding.project_id == args.project_id,
                Finding.status.in_(statuses),
            )
        )
        if args.run_id is not None:
            query = query.where(Finding.run_id == args.run_id)
        if has_words:
            # Any of the words (OR), ranked by how well each finding matches.
            document = func.to_tsvector(FTS_CONFIG, Finding.statement + " " + Finding.quote)
            words = cast(
                func.replace(cast(func.plainto_tsquery(FTS_CONFIG, args.query), Text), "&", "|"),
                TSQUERY,
            )
            query = query.where(document.op("@@")(words)).order_by(
                func.ts_rank_cd(document, words).desc(), Finding.created_at.desc()
            )
        else:
            query = query.order_by(Finding.created_at.desc())
        rows = (await session.execute(query.limit(args.limit))).all()

    if not rows:
        where = f' matching "{args.query}"' if has_words else ""
        return ToolResult(
            f"No findings{where} in this project. Findings come from research runs "
            "(start_research); search_sources looks through the saved sources themselves.",
            {"findings": []},
        )
    lines = []
    items = []
    for number, (finding, source) in enumerate(rows, start=1):
        label = source.title or source.url or source.filename or "untitled"
        page = f", page {finding.page}" if finding.page else ""
        note = f"\n  Verifier: {finding.judge_note}" if finding.judge_note else ""
        lines.append(
            f"[{number}] {finding.statement}\n"
            f'  Quote: "{finding.quote}"\n'
            f"  Source: {label}{page} ({source.url or 'uploaded file'}; source {source.id})\n"
            f"  Status: {finding.status}; finding {finding.id}; run {finding.run_id}{note}"
        )
        items.append(
            {
                "finding_id": str(finding.id),
                "run_id": str(finding.run_id),
                "statement": finding.statement,
                "quote": finding.quote,
                "status": finding.status,
                "judge_note": finding.judge_note,
                "page": finding.page,
                "source_id": str(source.id),
                "source_title": source.title,
                "source_url": source.url,
            }
        )
    header = (
        f"{len(rows)} finding(s). Every quote was checked word for word against its source; "
        '"verified" means the verifier also agreed the quote supports the statement.'
    )
    return ToolResult(
        f"{header}\n{untrusted('findings', chr(10).join(lines))}", {"findings": items}
    )


# --- Raw tools: the research agents' own tools ------------------------------------------

_PROJECT_ID_SCHEMA = {
    "type": "string",
    "format": "uuid",
    "description": "The project to work in (from create_project, start_research or "
    "list_projects). Fetched pages are saved to it as sources.",
}
# What fetched or searched content can do to the outside world: raw tools that only read
# Anvesha's own stored data are closed-world.
_RAW_TOOL_HINTS: dict[str, dict[str, bool]] = {
    "web_search": {"read_only": True, "open_world": True},
    "scholar_search": {"read_only": True, "open_world": True},
    "fetch_page": {"read_only": False, "open_world": True},
    "read_source": {"read_only": True},
    "search_sources": {"read_only": True},
    "verify_quote": {"read_only": True},
    "list_sources": {"read_only": True},
}


class _RawArgs(BaseModel):
    """Only project_id is checked here; the tool validates its own arguments."""

    model_config = ConfigDict(extra="allow")

    project_id: uuid.UUID


def _raw_tool(tool: Tool) -> McpTool:
    base = tool_parameters_schema(tool.args_model)
    schema = {
        **base,
        "properties": {"project_id": _PROJECT_ID_SCHEMA, **base.get("properties", {})},
        "required": ["project_id", *base.get("required", [])],
    }

    async def handler(ctx: McpContext, args: _RawArgs) -> ToolResult:
        services = ctx.services
        async with services.db.session() as session:
            await require_project(session, ctx.principal.workspace_id, args.project_id)
        tool_context = ToolContext(
            workspace_id=ctx.principal.workspace_id,
            project_id=args.project_id,
            db=services.db,
            settings=services.settings,
            http=services.http,
            vault=services.vault,
            gateway=services.gateway,
            sources=services.sources,
            # Another AI agent chooses what to fetch, so agent rules (robots.txt) apply.
            added_by="agent",
        )
        return await services.tools.invoke(tool.name, args.model_extra or {}, tool_context)

    hints = _RAW_TOOL_HINTS.get(tool.name, {})
    return McpTool(
        name=tool.name,
        title=tool.name.replace("_", " ").capitalize(),
        description=tool.description,
        args_model=_RawArgs,
        handler=handler,
        read_only=hints.get("read_only", False),
        open_world=hints.get("open_world", False),
        schema=schema,
    )


def build_mcp_tools(registry: ToolRegistry) -> list[McpTool]:
    research = [
        McpTool(
            "start_research",
            "Start research",
            "Have Anvesha's research agents investigate a question: they plan sub-questions, "
            "search the web and scholarly literature, read full sources, record findings "
            "backed by quotes checked word for word, and write a cited report. Runs in the "
            "background and uses the models (and API keys) configured in Anvesha, within a "
            "spending limit. Returns a run_id for get_research_status and get_report.",
            StartResearchArgs,
            _start_research,
            open_world=True,
        ),
        McpTool(
            "get_research_status",
            "Get research status",
            "Progress of a research run: status, the plan and each sub-question's coverage, "
            "findings so far, spending, and recent activity.",
            ResearchStatusArgs,
            _research_status,
            read_only=True,
        ),
        McpTool(
            "approve_plan",
            "Approve research plan",
            "Approve a run's research plan (status awaiting_approval) so the research "
            "continues, optionally replacing its sub-questions.",
            ApprovePlanArgs,
            _approve_plan,
        ),
        McpTool(
            "cancel_research",
            "Cancel research",
            "Stop a research run. Findings recorded so far are kept.",
            RunArgs,
            _cancel_research,
            destructive=True,
        ),
        McpTool(
            "get_report",
            "Get report",
            "Read a research report (Markdown with numbered citations). Every citation "
            "points to a quote verified word for word in its source.",
            GetReportArgs,
            _get_report,
            read_only=True,
        ),
        McpTool(
            "search_evidence",
            "Search evidence",
            "Search a project's findings: statements, each backed by a quote verified word "
            "for word in a saved source. Use it to answer follow-up questions with citations.",
            SearchEvidenceArgs,
            _search_evidence,
            read_only=True,
        ),
        McpTool(
            "list_projects",
            "List projects",
            "List research projects, newest first, with each one's latest run.",
            ListProjectsArgs,
            _list_projects,
            read_only=True,
        ),
        McpTool(
            "create_project",
            "Create project",
            "Create an empty research project, to research with the search, fetch and "
            "verify tools yourself (start_research creates one automatically).",
            CreateProjectArgs,
            _create_project,
        ),
    ]
    return research + [_raw_tool(tool) for tool in registry.tools()]
