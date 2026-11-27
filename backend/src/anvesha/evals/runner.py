"""Running eval cases through the real research pipeline, scoring them, and gating.

Each case becomes a project with its seed sources, and an autopilot run that this
process executes directly (not through the job queue, so a worker watching the same
database never picks it up). The finished run is then scored from what was saved.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from anvesha import tracing
from anvesha.db.models import (
    Finding,
    ModelPreference,
    Project,
    Report,
    Run,
    Source,
    SubQuestion,
    UsageEvent,
)
from anvesha.errors import AppError
from anvesha.evals import metrics
from anvesha.evals.suite import Case, Fact, Gate, Suite
from anvesha.graph.executor import RunExecutor
from anvesha.llm.catalog import PROVIDERS
from anvesha.llm.types import AGENT_ROLES, AgentRole
from anvesha.runs import create_run
from anvesha.services import Services
from anvesha.tenancy import Principal
from anvesha.verify.quotes import match_quote
from anvesha.worker.queue import JobQueue

# Higher is better for these; the gate and baseline comparisons use them.
QUALITY_METRICS = ("citation_precision", "cited_sentence_rate", "fact_recall")


class _InlineQueue(JobQueue):
    """Runs are executed by the eval process itself, so nothing is queued."""

    async def enqueue_run(self, run_id: uuid.UUID) -> None:
        return None


@dataclass
class CaseResult:
    case_id: str
    run_id: str | None = None
    run_status: str | None = None
    report_status: str | None = None
    error: str | None = None
    citation_precision: float | None = None
    citations: int = 0
    citations_supported: int = 0
    citations_invented: int = 0
    citations_unreviewed: int = 0
    cited_sentence_rate: float | None = None
    fact_recall: float | None = None
    facts_missed: list[str] = field(default_factory=list)
    sub_question_coverage: float | None = None
    findings_verified: int = 0
    findings_rejected: int = 0
    findings_unreviewed: int = 0
    seed_errors: list[str] = field(default_factory=list)
    cost_usd: Decimal = Decimal(0)
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    unpriced_calls: int = 0
    duration_seconds: float | None = None

    @property
    def completed(self) -> bool:
        return self.run_status == "completed" and self.error is None


@dataclass
class GateCheck:
    name: str
    value: float | None
    threshold: float
    passed: bool


@dataclass
class SuiteResult:
    suite: str
    models: dict[str, str]
    started_at: str
    cases: list[CaseResult]
    aggregate: dict[str, Any] = field(default_factory=dict)
    checks: list[GateCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_json(self) -> dict[str, Any]:
        def plain(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, dict):
                return {k: plain(v) for k, v in value.items()}
            if isinstance(value, list):
                return [plain(v) for v in value]
            return value

        data: dict[str, Any] = plain(
            {
                "suite": self.suite,
                "models": self.models,
                "started_at": self.started_at,
                "passed": self.passed,
                "aggregate": self.aggregate,
                "gate": [asdict(check) for check in self.checks],
                "cases": [asdict(case) for case in self.cases],
            }
        )
        return data


def parse_model(value: str) -> tuple[str, str]:
    """``provider:model`` (the model id may itself contain colons)."""
    provider, sep, model = value.partition(":")
    if not sep or not model:
        raise ValueError(f"expected provider:model, got {value!r}")
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; one of {', '.join(PROVIDERS)}")
    return provider, model


async def set_models(
    services: Services, workspace_id: uuid.UUID, models: dict[AgentRole, tuple[str, str]]
) -> None:
    async with services.db.session() as session:
        for role, (provider, model) in models.items():
            statement = insert(ModelPreference).values(
                workspace_id=workspace_id, role=role, provider=provider, model=model
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ModelPreference.workspace_id, ModelPreference.role],
                    set_={"provider": statement.excluded.provider, "model": model},
                )
            )
        await session.commit()


async def current_models(services: Services, workspace_id: uuid.UUID) -> dict[str, str]:
    async with services.db.session() as session:
        rows = await session.scalars(
            select(ModelPreference).where(ModelPreference.workspace_id == workspace_id)
        )
        return {row.role: f"{row.provider}:{row.model}" for row in rows if row.role in AGENT_ROLES}


async def score_run(services: Services, run_id: uuid.UUID, facts: list[Fact]) -> CaseResult:
    """Score a finished run from what it saved (works for any run, not just evals)."""
    db = services.db
    result = CaseResult(case_id="", run_id=str(run_id))
    async with db.session() as session:
        run = await session.get(Run, run_id)
        if run is None:
            result.error = "run not found"
            return result
        result.run_status = run.status
        if run.error_code:
            result.error = f"{run.error_code}: {run.error_message or ''}".strip()
        if run.started_at and run.finished_at:
            result.duration_seconds = (run.finished_at - run.started_at).total_seconds()
        report = await session.scalar(select(Report).where(Report.run_id == run_id))
        statuses = dict(
            (
                await session.execute(
                    select(Finding.status, func.count())
                    .where(Finding.run_id == run_id)
                    .group_by(Finding.status)
                )
            ).all()
        )
        coverages = (
            await session.scalars(select(SubQuestion.coverage).where(SubQuestion.run_id == run_id))
        ).all()
        usage = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                    func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                    func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                    func.count().filter(UsageEvent.cost_usd.is_(None)),
                ).where(UsageEvent.run_id == run_id)
            )
        ).one()
        cited_ids = {
            uuid.UUID(str(fid))
            for citation in (report.citations if report else [])
            for fid in citation.get("finding_ids", [])
        }
        cited_rows = (
            (
                await session.execute(
                    select(Finding.id, Finding.status, Finding.quote, Source.text)
                    .join(Source, Source.id == Finding.source_id)
                    .where(Finding.id.in_(cited_ids))
                )
            ).all()
            if cited_ids
            else []
        )

    result.findings_verified = int(statuses.get("verified", 0))
    result.findings_rejected = int(statuses.get("rejected", 0))
    result.findings_unreviewed = int(statuses.get("quote_verified", 0))
    result.sub_question_coverage = metrics.sub_question_coverage(coverages)
    calls, cost, tokens_in, tokens_out, unpriced = usage
    result.model_calls = int(calls)
    result.cost_usd = Decimal(cost or 0)
    result.input_tokens, result.output_tokens = int(tokens_in), int(tokens_out)
    result.unpriced_calls = int(unpriced)
    if report is None:
        if result.error is None and run.status == "completed":
            result.error = "the run completed without a report"
        return result

    result.report_status = report.status
    cited = [
        metrics.CitedFinding(str(fid), status, match_quote(quote, text).supported)
        for fid, status, quote, text in cited_rows
    ]
    # Findings deleted since the report was written count as unsupported.
    cited += [
        metrics.CitedFinding(str(fid), "missing", False)
        for fid in cited_ids - {row[0] for row in cited_rows}
    ]
    score = metrics.citation_precision(cited, len(report.details.get("dropped_citations", [])))
    result.citation_precision = score.precision
    result.citations, result.citations_supported = score.cited, score.supported
    result.citations_invented, result.citations_unreviewed = score.invented, score.unreviewed
    result.cited_sentence_rate = metrics.cited_sentence_rate(report.markdown)
    result.fact_recall, result.facts_missed = metrics.fact_recall(report.markdown, facts)
    return result


async def run_case(
    services: Services,
    executor: RunExecutor,
    principal: Principal,
    suite: Suite,
    case: Case,
) -> CaseResult:
    started = datetime.now(UTC)
    with tracing.span(
        f"eval {case.id}",
        kind="evaluator",
        attributes=tracing.trace_attributes(
            name=f"eval {suite.name}/{case.id}",
            tags=["eval", f"suite:{suite.name}", f"case:{case.id}"],
        ),
        input={"question": case.question},
    ) as span:
        result = await _run_case(services, executor, principal, suite, case)
        tracing.set_attributes(
            span,
            {
                f"anvesha.eval.{name}": getattr(result, name)
                for name in (*QUALITY_METRICS, "cost_usd", "run_status")
            },
        )
    if result.duration_seconds is None:
        result.duration_seconds = (datetime.now(UTC) - started).total_seconds()
    return result


async def _run_case(
    services: Services,
    executor: RunExecutor,
    principal: Principal,
    suite: Suite,
    case: Case,
) -> CaseResult:
    async with services.db.session() as session:
        project = Project(
            workspace_id=principal.workspace_id,
            created_by_id=principal.user_id,
            title=f"[eval] {suite.name}/{case.id}"[:255],
            objective=case.question,
            depth=suite.depth(case),
        )
        session.add(project)
        await session.commit()
        project_id = project.id

    seed_errors: list[str] = []
    for url in case.urls:
        try:
            await services.sources.add_from_url(principal.workspace_id, project_id, url)
        except AppError as exc:
            seed_errors.append(f"{url}: {exc.code}: {exc}")

    try:
        async with services.db.session() as session:
            run = await create_run(
                session,
                settings=services.settings,
                vault=services.vault,
                queue=_InlineQueue(services.settings),
                principal=principal,
                project_id=project_id,
                question=None,
                depth=None,
                autopilot=True,
                max_cost_usd=suite.budget(case),
                max_tokens=None,
            )
            run_id = run.id
    except AppError as exc:
        return CaseResult(case_id=case.id, error=f"{exc.code}: {exc}", seed_errors=seed_errors)

    await executor.execute(run_id)
    result = await score_run(services, run_id, case.facts)
    result.case_id = case.id
    result.seed_errors = seed_errors
    return result


async def run_suite(
    services: Services,
    executor: RunExecutor,
    principal: Principal,
    suite: Suite,
    cases: list[Case],
    *,
    parallel: int = 1,
    on_result: Callable[[CaseResult], None] | None = None,
) -> SuiteResult:
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    semaphore = asyncio.Semaphore(max(parallel, 1))

    async def one(case: Case) -> CaseResult:
        async with semaphore:
            result = await run_case(services, executor, principal, suite, case)
        if on_result is not None:
            on_result(result)
        return result

    results = await asyncio.gather(*(one(case) for case in cases))
    outcome = SuiteResult(
        suite=suite.name,
        models=await current_models(services, principal.workspace_id),
        started_at=started_at,
        cases=list(results),
    )
    outcome.aggregate = aggregate(outcome.cases)
    return outcome


def aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    completed = [c for c in cases if c.completed]
    costs = [c.cost_usd for c in cases]
    return {
        "cases": len(cases),
        "failed_cases": len(cases) - len(completed),
        **{name: metrics.mean(getattr(c, name) for c in completed) for name in QUALITY_METRICS},
        "sub_question_coverage": metrics.mean(c.sub_question_coverage for c in completed),
        "total_cost_usd": sum(costs, Decimal(0)),
        "mean_cost_usd": sum(costs, Decimal(0)) / len(costs) if costs else Decimal(0),
        "max_case_cost_usd": max(costs, default=Decimal(0)),
        "unpriced_calls": sum(c.unpriced_calls for c in cases),
        "mean_duration_seconds": metrics.mean(c.duration_seconds for c in cases),
    }


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def gate_checks(
    aggregate_: dict[str, Any], gate: Gate, baseline: dict[str, Any] | None = None
) -> list[GateCheck]:
    """The gate's verdict on a suite's aggregate metrics (and against a baseline's)."""
    checks: list[GateCheck] = []

    def at_least(name: str, metric: str, threshold: float | None) -> None:
        if threshold is None:
            return
        value = aggregate_.get(metric)
        checks.append(
            GateCheck(name, value, threshold, value is not None and value >= threshold - 1e-9)
        )

    def at_most(name: str, metric: str, threshold: Decimal | int | None) -> None:
        if threshold is None:
            return
        value = _decimal(aggregate_.get(metric))
        checks.append(
            GateCheck(
                name,
                None if value is None else float(value),
                float(threshold),
                value is not None and value <= Decimal(threshold),
            )
        )

    at_least("citation precision", "citation_precision", gate.min_citation_precision)
    at_least("cited-sentence rate", "cited_sentence_rate", gate.min_cited_sentence_rate)
    at_least("fact recall", "fact_recall", gate.min_fact_recall)
    at_most("mean cost per case (USD)", "mean_cost_usd", gate.max_mean_cost_usd)
    at_most("highest case cost (USD)", "max_case_cost_usd", gate.max_case_cost_usd)
    at_most("failed cases", "failed_cases", gate.max_failed_cases)

    if baseline:
        for metric in QUALITY_METRICS:
            before = baseline.get(metric)
            if before is not None:
                at_least(
                    f"{metric.replace('_', ' ')} vs baseline",
                    metric,
                    max(float(before) - gate.max_regression, 0.0),
                )
        before_cost = _decimal(baseline.get("mean_cost_usd"))
        if before_cost:
            at_most(
                "mean cost vs baseline (USD)",
                "mean_cost_usd",
                before_cost * Decimal(str(1 + gate.max_cost_increase)),
            )
    return checks


def _fmt(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "-"
    if percent:
        return f"{float(value):.0%}"
    if isinstance(value, Decimal):
        return f"${value:.4f}"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def markdown_summary(result: SuiteResult) -> str:
    verdict = "✅ passed" if result.passed else "❌ failed"
    if not result.checks:
        verdict = "no gate"
    models = ", ".join(f"{role} `{model}`" for role, model in sorted(result.models.items()))
    agg = result.aggregate
    lines = [
        f"## Eval `{result.suite}`: {verdict}",
        "",
        f"Models: {models or 'deployment defaults'}  ",
        f"Cases: {agg['cases']} ({agg['failed_cases']} failed) · total cost "
        f"{_fmt(agg['total_cost_usd'])} · started {result.started_at}",
        "",
    ]
    if result.checks:
        lines += ["| Check | Value | Threshold | |", "|---|---|---|---|"]
        for check in result.checks:
            lines.append(
                f"| {check.name} | {_fmt(check.value)} | {_fmt(check.threshold)} | "
                f"{'✅' if check.passed else '❌'} |"
            )
        lines.append("")
    lines += [
        "| Case | Run | Citation precision | Cited sentences | Fact recall | Coverage | "
        "Findings ✓/✗ | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in result.cases:
        lines.append(
            f"| `{case.case_id}` | {case.run_status or 'not started'} | "
            f"{_fmt(case.citation_precision, percent=True)} | "
            f"{_fmt(case.cited_sentence_rate, percent=True)} | "
            f"{_fmt(case.fact_recall, percent=True)} | "
            f"{_fmt(case.sub_question_coverage, percent=True)} | "
            f"{case.findings_verified}/{case.findings_rejected} | {_fmt(case.cost_usd)} |"
        )
    problems = [
        (case.case_id, note)
        for case in result.cases
        for note in (
            ([case.error] if case.error else [])
            + [f"missed fact: {claim}" for claim in case.facts_missed]
            + [f"seed source failed: {err}" for err in case.seed_errors]
            + (
                [f"{case.citations_invented} invented citation marker(s)"]
                if case.citations_invented
                else []
            )
        )
    ]
    if problems:
        lines += ["", "<details><summary>Notes</summary>", ""]
        lines += [f"- `{case_id}`: {note}" for case_id, note in problems]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"
