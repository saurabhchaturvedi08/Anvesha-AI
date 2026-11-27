"""Run the eval suite, or score an existing run.

    uv run python -m anvesha.evals run ../evals/golden.toml --model openai:gpt-4o-mini
    uv run python -m anvesha.evals score <run id> [--suite ../evals/golden.toml --case crispr]

``run`` uses its own database (``anvesha_eval`` on the configured server, or
``ANVESHA_EVAL_DATABASE_URL``), created and migrated on first use, so eval projects never
show up in your own workspace. Model keys come from the usual ``ANVESHA_*_API_KEY``
settings. Exit codes: 0 passed, 1 the gate failed, 2 the eval couldn't run.
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from anvesha.config import Settings, get_settings
from anvesha.errors import AppError
from anvesha.evals.runner import (
    CaseResult,
    SuiteResult,
    gate_checks,
    markdown_summary,
    parse_model,
    run_suite,
    score_run,
    set_models,
)
from anvesha.evals.suite import Suite, load_suite
from anvesha.graph.checkpointer import open_checkpointer
from anvesha.graph.executor import RunExecutor
from anvesha.llm.errors import ModelNotConfiguredError
from anvesha.llm.types import AGENT_ROLES, AgentRole
from anvesha.migrate import migrate, run_async
from anvesha.runs import check_models
from anvesha.services import open_services
from anvesha.tenancy import ensure_local_principal

logger = logging.getLogger("anvesha.evals")

EVAL_DATABASE = "anvesha_eval"


def eval_database_url(settings: Settings, override: str | None) -> str:
    url = override or os.environ.get("ANVESHA_EVAL_DATABASE_URL")
    if url:
        return url
    return (
        make_url(settings.database_url)
        .set(database=EVAL_DATABASE)
        .render_as_string(hide_password=False)
    )


async def create_database_if_missing(url: str) -> None:
    target = make_url(url)
    engine = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            )
            if not exists:
                logger.info("Creating database %s", target.database)
                await connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await engine.dispose()


def _models(args: argparse.Namespace) -> dict[AgentRole, tuple[str, str]]:
    models: dict[AgentRole, tuple[str, str]] = {}
    if args.model:
        models = dict.fromkeys(AGENT_ROLES, parse_model(args.model))
    for item in args.role_model or []:
        role, sep, value = item.partition("=")
        if not sep or role not in AGENT_ROLES:
            raise ValueError(f"--role-model takes role=provider:model, roles: {AGENT_ROLES}")
        models[role] = parse_model(value)
    return models


def _print_case(result: CaseResult) -> None:
    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.0%}"

    print(
        f"  {result.case_id}: {result.run_status or 'not started'}"
        f" · precision {pct(result.citation_precision)}"
        f" · cited {pct(result.cited_sentence_rate)}"
        f" · facts {pct(result.fact_recall)}"
        f" · ${result.cost_usd:.4f}" + (f" · {result.error}" if result.error else ""),
        flush=True,
    )


async def _run(
    args: argparse.Namespace,
    settings: Settings,
    suite: Suite,
    models: dict[AgentRole, tuple[str, str]],
) -> SuiteResult:
    cases = suite.select(args.case or [])
    async with (
        open_services(settings, service="anvesha-evals") as services,
        open_checkpointer(settings) as checkpointer,
    ):
        async with services.db.session() as session:
            principal = await ensure_local_principal(session, settings.local_user_email)
        await set_models(services, principal.workspace_id, models)
        async with services.db.session() as session:
            # Fail once, clearly, instead of once per case after fetching its sources.
            try:
                await check_models(session, settings, services.vault, principal.workspace_id)
            except ModelNotConfiguredError as exc:
                raise ValueError(
                    f"{exc} For evals, pass --model provider:model (and --role-model "
                    "role=provider:model), with the provider's key in ANVESHA_*_API_KEY."
                ) from exc
        print(f"Running {len(cases)} case(s) from {suite.name}...", flush=True)
        return await run_suite(
            services,
            RunExecutor(services, checkpointer),
            principal,
            suite,
            cases,
            parallel=args.parallel,
            on_result=_print_case,
        )


def command_run(args: argparse.Namespace) -> int:
    # Everything that can be wrong with the arguments is checked before connecting.
    suite = load_suite(args.suite)
    suite.select(args.case or [])
    models = _models(args)
    baseline: dict[str, Any] | None = None
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["aggregate"]

    base = get_settings()
    settings = base.model_copy(update={"database_url": eval_database_url(base, args.database_url)})
    run_async(lambda: create_database_if_missing(settings.database_url))
    migrate(settings)
    result = run_async(lambda: _run(args, settings, suite, models))

    if not args.no_gate:
        result.checks = gate_checks(result.aggregate, suite.gate, baseline)

    summary = markdown_summary(result)
    print("\n" + summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result.to_json(), indent=2) + "\n", encoding="utf-8")
        print(f"Results written to {args.out}")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as file:
            file.write(summary)
    return 0 if result.passed else 1


async def _score(args: argparse.Namespace, settings: Settings) -> CaseResult:
    facts = []
    if args.suite and args.case:
        facts = load_suite(args.suite).select([args.case])[0].facts
    async with open_services(settings, service="anvesha-evals") as services:
        result = await score_run(services, args.run_id, facts)
    result.case_id = args.case or str(args.run_id)
    return result


def command_score(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    result = run_async(lambda: _score(args, settings))
    print(json.dumps(SuiteResult("", {}, "", [result]).to_json()["cases"][0], indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m anvesha.evals", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run an eval suite and check its gate")
    run.add_argument("suite", type=Path, help="suite file, e.g. ../evals/golden.toml")
    run.add_argument("--case", action="append", help="only this case (repeatable)")
    run.add_argument("--model", help="provider:model for every agent role")
    run.add_argument(
        "--role-model", action="append", help="role=provider:model for one role (repeatable)"
    )
    run.add_argument("--parallel", type=int, default=1, help="cases to run at the same time")
    run.add_argument("--out", type=Path, help="write results JSON here")
    run.add_argument("--summary", type=Path, help="append the Markdown summary to this file")
    run.add_argument("--baseline", type=Path, help="earlier results JSON to compare against")
    run.add_argument("--no-gate", action="store_true", help="report only; always exit 0")
    run.add_argument("--database-url", help="database for eval projects")
    run.set_defaults(handler=command_run)

    score = commands.add_parser("score", help="score an existing run")
    score.add_argument("run_id", type=uuid.UUID)
    score.add_argument("--suite", type=Path, help="suite with the case's expected facts")
    score.add_argument("--case", help="case id whose facts to check")
    score.add_argument("--database-url", help="database the run is in (default: configured)")
    score.set_defaults(handler=command_score)

    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ValueError, OSError, AppError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
