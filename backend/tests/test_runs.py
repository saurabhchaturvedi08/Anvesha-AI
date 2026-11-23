"""End-to-end research runs with a scripted model: the real graph, checkpoints, job
queue and database, with model calls and websites faked."""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text, update

from anvesha.config import Settings
from anvesha.db.models import Finding, ModelPreference, Report, Run, SubQuestion, UsageEvent
from anvesha.graph.checkpointer import open_checkpointer
from anvesha.graph.executor import RetryRun, RunExecutor
from anvesha.llm.errors import LLMAuthError, LLMRateLimitError
from anvesha.llm.gateway import LLMGateway
from anvesha.llm.types import Completion, Message, ModelRoute, TokenUsage, ToolCall, ToolSpec
from anvesha.services import Services
from anvesha.worker.queue import RUN_TASK, JobQueue
from tests.conftest import FakeWeb, _rebuild_source_service, make_settings

SOLAR_URL = "https://energy.example/solar-2025"
WIND_URL = "https://energy.example/wind-2025"
STORAGE_URL = "https://energy.example/storage-2025"
SOLAR_PAGE = """<html><head><title>Solar in 2025</title></head><body><article>
<h1>Solar in 2025</h1>
<p>Global solar capacity grew by 24 percent in 2025, according to the agency's annual report.</p>
<p>Module prices fell for the third year in a row as factories expanded production.</p>
</article></body></html>"""
WIND_PAGE = """<html><head><title>Wind in 2025</title></head><body><article>
<h1>Wind in 2025</h1>
<p>Wind power capacity grew by only 6 percent in 2025 because supply chains remained tight.</p>
<p>Offshore projects were delayed in several markets by higher interest rates.</p>
</article></body></html>"""
STORAGE_PAGE = """<html><head><title>Storage in 2025</title></head><body><article>
<h1>Storage in 2025</h1>
<p>Battery storage installations doubled in 2025 as costs for lithium cells kept falling.</p>
</article></body></html>"""

# What each researcher reads and quotes, keyed by a word in its sub-question.
SCRIPTS = {
    "solar": (
        SOLAR_URL,
        "Global solar capacity grew 24% in 2025.",
        "Global solar capacity grew by 24 percent in 2025",
    ),
    "wind": (
        WIND_URL,
        "Wind capacity grew only 6% in 2025 due to tight supply chains.",
        "Wind power capacity grew by only 6 percent in 2025 because supply chains remained tight",
    ),
    "storage": (
        STORAGE_URL,
        "Battery storage installations doubled in 2025.",
        "Battery storage installations doubled in 2025",
    ),
}


class ScriptedModel:
    """Plays every agent, recognised by the tools it is offered."""

    def __init__(self) -> None:
        self.plan = [
            "How fast did solar capacity grow in 2025?",
            "How fast did wind capacity grow in 2025?",
        ]
        self.follow_ups: list[str] = []
        self.reject_words: set[str] = set()  # the judge rejects statements containing these
        self.paraphrase_first = False  # researchers first try a quote that isn't verbatim
        self.writer_text: str | None = None
        self.errors: dict[str, Exception] = {}  # agent -> error to raise
        self.on_call: Callable[[str], Any] | None = None
        self.calls: list[str] = []
        self.usage = TokenUsage(input_tokens=1000, output_tokens=500)

    async def complete(
        self,
        route: ModelRoute,
        messages: list[Message],
        *,
        tools: list[ToolSpec] = (),  # type: ignore[assignment]
        max_tokens: int | None = None,
        temperature: float | None = None,
        request_timeout: float,
    ) -> Completion:
        names = {tool.name for tool in tools}
        agent = (
            "lead_plan"
            if "submit_plan" in names
            else "lead_review"
            if "submit_review" in names
            else "verifier"
            if "submit_verdicts" in names
            else "researcher"
            if "record_finding" in names
            else "writer"
        )
        self.calls.append(agent)
        if self.on_call is not None:
            await self.on_call(agent)
        if agent in self.errors:
            raise self.errors[agent]
        handler = getattr(self, f"_{agent}")
        text_out, calls = handler(messages)
        return Completion(
            text=text_out,
            tool_calls=tuple(calls),
            usage=self.usage,
            provider=route.access.provider,
            model=route.model,
            finish_reason="tool_calls" if calls else "stop",
        )

    @staticmethod
    def _call(name: str, **arguments: Any) -> ToolCall:
        return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=arguments)

    def _lead_plan(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        plan = {
            "strategy": "Prefer agency reports.",
            "sub_questions": [{"question": q, "rationale": "Core trend."} for q in self.plan],
        }
        return "", [self._call("submit_plan", **plan)]

    def _lead_review(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        follow_ups = [{"question": q, "rationale": "Gap."} for q in self.follow_ups]
        self.follow_ups = []  # only once
        return "", [
            self._call(
                "submit_review",
                assessment="Good coverage.",
                contradictions=[
                    {"description": "Growth rates differ by technology.", "findings": [1, 2]}
                ],
                follow_up_questions=follow_ups,
            )
        ]

    def _verifier(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        items = re.findall(r"Item (\d+)\nStatement: (.+)", messages[-1].content)
        verdicts = [
            {
                "item": int(number),
                "supports": not any(word in statement for word in self.reject_words),
                "note": "checked",
            }
            for number, statement in items
        ]
        return "", [self._call("submit_verdicts", verdicts=verdicts)]

    def _researcher(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        task = messages[1].content
        sub_question = task.split("Your sub-question:\n", 1)[1].split("\n", 1)[0].lower()
        key = next(word for word in SCRIPTS if word in sub_question)
        url, statement, quote = SCRIPTS[key]
        tool_results = [m.content for m in messages if m.role == "tool"]
        if not tool_results:
            return "Reading the main source.", [self._call("fetch_page", url=url)]
        source_ids = re.findall(r"saved as source ([0-9a-f-]{36})", " ".join(tool_results), re.I)
        if not any("Recorded finding" in r for r in tool_results):
            if self.paraphrase_first and not any("REJECTED" in r for r in tool_results):
                return "", [
                    self._call(
                        "record_finding",
                        statement=statement,
                        quote=quote + " worldwide",  # not in the source
                        source_id=source_ids[0],
                    )
                ]
            return "", [
                self._call(
                    "record_finding", statement=statement, quote=quote, source_id=source_ids[0]
                )
            ]
        return "", [
            self._call("finish", summary=f"Evidence found for {key}.", coverage="answered", gaps=[])
        ]

    def _writer(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        if self.writer_text is not None:
            return self.writer_text, []
        return (
            "# Renewables in 2025\n\n## Summary\n\nSolar grew fast [F1] while wind lagged [F2, F9].\n\n"
            "## Findings\n\n### Growth\n\nSolar led growth [F1].\n\n## Open questions\n\nNone.\n\n"
            "## References\n\n1. Made-up source\n",
            [],
        )


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return make_settings(database_url=database_url, openai_api_key="sk-deployment")


@pytest.fixture
async def model(app: FastAPI, fake_web: FakeWeb) -> ScriptedModel:
    fake_web.page(SOLAR_URL, SOLAR_PAGE)
    fake_web.page(WIND_URL, WIND_PAGE)
    fake_web.page(STORAGE_URL, STORAGE_PAGE)
    scripted = ScriptedModel()
    app.state.gateway = LLMGateway(
        app.state.db,
        app.state.settings,
        app.state.vault,
        {"openai": scripted, "anthropic": scripted},
    )
    _rebuild_source_service(app)
    async with app.state.db.session() as session:
        for role in ("lead", "researcher", "verifier", "writer"):
            session.add(
                ModelPreference(
                    workspace_id=app.state.local_principal.workspace_id,
                    role=role,
                    provider="openai",
                    model="gpt-4o-mini",
                )
            )
        await session.commit()
    return scripted


@pytest.fixture
async def executor(app: FastAPI, model: ScriptedModel) -> AsyncIterator[RunExecutor]:
    state = app.state
    services = Services(
        settings=state.settings,
        db=state.db,
        vault=state.vault,
        http=state.http,
        fetcher=state.fetcher,
        storage=state.storage,
        gateway=state.gateway,
        sources=state.sources,
        tools=state.tools,
    )
    async with open_checkpointer(state.settings) as checkpointer:
        yield RunExecutor(services, checkpointer)


async def _project(client: AsyncClient, depth: str = "quick") -> str:
    response = await client.post(
        "/api/v1/projects",
        json={
            "title": "Renewables",
            "objective": "How did renewables grow in 2025?",
            "depth": depth,
        },
    )
    return str(response.json()["id"])


async def _start(client: AsyncClient, project_id: str, **body: Any) -> dict[str, Any]:
    response = await client.post(f"/api/v1/projects/{project_id}/runs", json=body)
    assert response.status_code == 202, response.text
    return dict(response.json())


async def _run(client: AsyncClient, run_id: str) -> dict[str, Any]:
    return dict((await client.get(f"/api/v1/runs/{run_id}")).json())


async def _event_types(client: AsyncClient, run_id: str) -> list[str]:
    events = (await client.get(f"/api/v1/runs/{run_id}/events", params={"limit": 1000})).json()
    return [event["type"] for event in events]


async def _queued_jobs(app: FastAPI, run_id: str) -> int:
    async with app.state.db.session() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = :task "
                "AND args->>'run_id' = :run_id AND status = 'todo'"
            ),
            {"task": RUN_TASK, "run_id": run_id},
        )
    return int(count or 0)


async def test_run_with_plan_approval_produces_a_cited_report(
    app: FastAPI, client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    model.reject_words = {"Wind"}  # the judge rejects the wind finding
    project_id = await _project(client)
    run = await _start(client, project_id)
    assert run["status"] == "queued" and run["question"] == "How did renewables grow in 2025?"
    assert await _queued_jobs(app, run["id"]) == 1

    # First pass: the lead plans, then the run waits for approval.
    await executor.execute(uuid.UUID(run["id"]))
    paused = await _run(client, run["id"])
    assert paused["status"] == "awaiting_approval"
    assert [q["question"] for q in paused["plan"]["sub_questions"]] == model.plan
    assert model.calls == ["lead_plan"]

    approved = await client.post(f"/api/v1/runs/{run['id']}/approve", json={})
    assert approved.status_code == 202 and approved.json()["status"] == "queued"
    await executor.execute(uuid.UUID(run["id"]))

    done = await _run(client, run["id"])
    assert done["status"] == "completed", done
    assert model.calls.count("lead_plan") == 1  # resumed, not re-planned
    assert done["findings"] == {"verified": 1, "rejected": 1}
    assert {sq["status"] for sq in done["sub_questions"]} == {"done"}
    assert done["usage"]["calls"] == len(model.calls) and Decimal(done["usage"]["cost_usd"]) > 0

    report = (await client.get(f"/api/v1/runs/{run['id']}/report")).json()
    assert report["status"] == "complete" and report["title"] == "Renewables in 2025"
    # [F1] is the verified solar finding; [F2] was rejected and [F9] never existed, so
    # neither may be cited. The model's own reference list is replaced by ours.
    assert "Solar grew fast [1] while wind lagged." in report["markdown"]
    assert "Made-up source" not in report["markdown"]
    assert report["markdown"].count("## Sources") == 1
    assert f"<{SOLAR_URL}>" in report["markdown"] and WIND_URL not in report["markdown"]
    assert report["details"]["dropped_citations"] == ["F2", "F9"]
    assert [c["url"] for c in report["citations"]] == [SOLAR_URL]

    types = await _event_types(client, run["id"])
    for expected in (
        "run_queued",
        "run_started",
        "plan_created",
        "plan_ready",
        "plan_approval_received",
        "plan_approved",
        "researcher_started",
        "tool_call",
        "finding_recorded",
        "verification_finished",
        "finding_rejected",
        "run_completed",
    ):
        assert expected in types, expected
    assert types[-1] == "run_completed"

    async with app.state.db.session() as session:
        tagged = await session.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.run_id == uuid.UUID(run["id"]))
        )
    assert tagged == len(model.calls)

    listed = (await client.get(f"/api/v1/projects/{project_id}/reports")).json()
    assert [(r["version"], r["id"]) for r in listed] == [(1, report["id"])]


async def test_edited_plan_is_researched_instead(
    client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    project_id = await _project(client)
    run = await _start(client, project_id)
    await executor.execute(uuid.UUID(run["id"]))
    edited = [{"question": "How much did battery storage grow in 2025?", "rationale": "Mine."}]
    response = await client.post(
        f"/api/v1/runs/{run['id']}/approve", json={"sub_questions": edited}
    )
    assert response.status_code == 202
    await executor.execute(uuid.UUID(run["id"]))

    done = await _run(client, run["id"])
    assert [sq["question"] for sq in done["sub_questions"]] == [edited[0]["question"]]
    assert done["plan"]["sub_questions"][0]["question"] == edited[0]["question"]
    assert done["findings"] == {"verified": 1}


async def test_autopilot_follow_up_round_and_quote_correction(
    client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    model.follow_ups = ["How much did battery storage grow in 2025?"]
    model.paraphrase_first = True  # every researcher's first quote is rejected and fixed
    model.writer_text = "# Report\n\nSolar [F1], wind [F2], storage [F3]."
    project_id = await _project(client, depth="standard")
    run = await _start(client, project_id, autopilot=True)
    await executor.execute(uuid.UUID(run["id"]))

    done = await _run(client, run["id"])
    assert done["status"] == "completed"
    assert [(sq["round"], sq["ordinal"]) for sq in done["sub_questions"]] == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]
    assert done["findings"] == {"verified": 3}
    # Review after round 1 (follow-up proposed); round 2 is the last, so no second review.
    assert model.calls.count("lead_review") == 1
    types = await _event_types(client, run["id"])
    assert "plan_ready" not in types and types.count("finding_rejected") == 3
    report = (await client.get(f"/api/v1/runs/{run['id']}/report")).json()
    assert report["markdown"].startswith("# Report\n\nSolar [1], wind [2], storage [3].")


async def test_budget_runs_out_and_a_partial_report_is_still_written(
    client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    model.usage = TokenUsage(input_tokens=200_000, output_tokens=100_000)  # pricey calls
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True, max_cost_usd="0.01")
    await executor.execute(uuid.UUID(run["id"]))

    done = await _run(client, run["id"])
    assert done["status"] == "completed"
    # Planning used up the research share of the budget: researchers stopped at once.
    assert model.calls == ["lead_plan"]
    report = (await client.get(f"/api/v1/runs/{run['id']}/report")).json()
    assert report["status"] == "incomplete"
    assert report["details"]["stop_reason"] == "budget"
    assert report["details"]["mode"] == "no_evidence"
    assert "No verified evidence was found" in report["markdown"]


async def test_cancelling_queued_and_running_runs(
    app: FastAPI, client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    project_id = await _project(client)
    queued = await _start(client, project_id, autopilot=True)
    cancelled = await client.post(f"/api/v1/runs/{queued['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    await executor.execute(uuid.UUID(queued["id"]))  # the queued job finds nothing to do
    assert model.calls == []
    again = await client.post(f"/api/v1/runs/{queued['id']}/cancel")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_run_state")

    running = await _start(client, project_id, autopilot=True)

    async def cancel_during_research(agent: str) -> None:
        if agent == "researcher":
            async with app.state.db.session() as session:
                await session.execute(
                    update(Run)
                    .where(Run.id == uuid.UUID(running["id"]))
                    .values(cancel_requested=True)
                )
                await session.commit()

    model.on_call = cancel_during_research
    await executor.execute(uuid.UUID(running["id"]))
    done = await _run(client, running["id"])
    assert done["status"] == "cancelled" and done["finished_at"] is not None
    assert (await _event_types(client, running["id"]))[-1] == "run_cancelled"


async def test_fatal_error_fails_the_run_and_retry_continues_from_the_checkpoint(
    client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    model.errors["researcher"] = LLMAuthError("openai: invalid key")
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    await executor.execute(uuid.UUID(run["id"]))
    failed = await _run(client, run["id"])
    assert (failed["status"], failed["error_code"]) == ("failed", "provider_auth_failed")
    # The project is free again for a new run while this one is failed.

    model.errors.clear()
    retried = await client.post(f"/api/v1/runs/{run['id']}/retry")
    assert retried.status_code == 202 and retried.json()["status"] == "queued"
    await executor.execute(uuid.UUID(run["id"]))
    done = await _run(client, run["id"])
    assert done["status"] == "completed" and done["attempts"] == 2
    assert model.calls.count("lead_plan") == 1  # the plan from the first attempt was kept


async def test_temporary_failure_is_retried_by_the_queue(
    client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    model.errors["writer"] = LLMRateLimitError("slow down")
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    with pytest.raises(RetryRun):
        await executor.execute(uuid.UUID(run["id"]), final_attempt=False)
    waiting = await _run(client, run["id"])
    assert waiting["status"] == "queued"
    assert "run_retrying" in await _event_types(client, run["id"])

    model.errors.clear()
    await executor.execute(uuid.UUID(run["id"]))
    assert (await _run(client, run["id"]))["status"] == "completed"
    assert model.calls.count("researcher") == 6  # research wasn't repeated: 3 steps x 2

    # On the last attempt, the same failure fails the run instead.
    second = await _start(client, project_id, autopilot=True)
    model.errors["writer"] = LLMRateLimitError("still slow")
    await executor.execute(uuid.UUID(second["id"]), final_attempt=True)
    assert (await _run(client, second["id"]))["error_code"] == "provider_rate_limited"


async def test_run_preconditions(client: AsyncClient, app: FastAPI, model: ScriptedModel) -> None:
    project_id = await _project(client)
    first = await _start(client, project_id)
    busy = await client.post(f"/api/v1/projects/{project_id}/runs", json={})
    assert (busy.status_code, busy.json()["code"]) == (409, "run_in_progress")

    too_much = await client.post(
        f"/api/v1/projects/{await _project(client)}/runs", json={"max_cost_usd": "1000"}
    )
    assert (too_much.status_code, too_much.json()["code"]) == (422, "budget_too_high")

    approve_early = await client.post(f"/api/v1/runs/{first['id']}/approve", json={})
    assert approve_early.status_code == 409
    retry_active = await client.post(f"/api/v1/runs/{first['id']}/retry")
    assert retry_active.status_code == 409

    async with app.state.db.session() as session:
        await session.execute(
            text("DELETE FROM model_preferences WHERE role IN ('writer', 'verifier')")
        )
        await session.commit()
    missing = await client.post(f"/api/v1/projects/{await _project(client)}/runs", json={})
    body = missing.json()
    assert (missing.status_code, body["code"]) == (409, "model_not_configured")
    assert "verifier role" in body["detail"] and "writer role" in body["detail"]

    unknown = uuid.uuid4()
    assert (await client.get(f"/api/v1/runs/{unknown}")).status_code == 404
    assert (await client.get(f"/api/v1/runs/{unknown}/events/stream")).status_code == 404


async def test_event_stream_replays_and_ends(
    client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    await executor.execute(uuid.UUID(run["id"]))
    events = (await client.get(f"/api/v1/runs/{run['id']}/events", params={"limit": 1000})).json()

    async def read_stream(**kwargs: Any) -> list[tuple[str, dict[str, Any], str | None]]:
        frames = []
        async with client.stream(
            "GET", f"/api/v1/runs/{run['id']}/events/stream", **kwargs
        ) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            body = (await response.aread()).decode()
        for block in body.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
            if "event" in fields:
                frames.append((fields["event"], json.loads(fields["data"]), fields.get("id")))
        return frames

    frames = await read_stream()
    assert [f[0] for f in frames] == [e["type"] for e in events] + ["end"]
    assert frames[-1][1] == {"status": "completed"}
    assert [int(f[2]) for f in frames[:-1]] == [e["id"] for e in events]

    # Reconnecting with Last-Event-ID only replays what came after it.
    resumed = await read_stream(headers={"Last-Event-ID": str(events[-3]["id"])})
    assert [f[0] for f in resumed] == [e["type"] for e in events[-2:]] + ["end"]


async def test_findings_and_reports_are_scoped_to_the_workspace(
    app: FastAPI, client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    await executor.execute(uuid.UUID(run["id"]))
    findings = (await client.get(f"/api/v1/runs/{run['id']}/findings")).json()
    assert len(findings) == 2 and all(f["spans"] for f in findings)
    rejected = (
        await client.get(f"/api/v1/runs/{run['id']}/findings", params={"status": "rejected"})
    ).json()
    assert rejected == []

    # Rows of another workspace are invisible.
    async with app.state.db.session() as session:
        other = uuid.uuid4()
        await session.execute(
            text("INSERT INTO workspaces (id, name) VALUES (:id, 'Other')"), {"id": other}
        )
        await session.execute(update(Run).values(workspace_id=other))
        await session.execute(update(Report).values(workspace_id=other))
        await session.commit()
    assert (await client.get(f"/api/v1/runs/{run['id']}")).status_code == 404
    assert (await client.get(f"/api/v1/runs/{run['id']}/findings")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{project_id}/reports")).json() == []


async def test_recovery_requeues_runs_without_a_heartbeat(
    app: FastAPI, client: AsyncClient, model: ScriptedModel
) -> None:
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    queue: JobQueue = app.state.queue
    async with app.state.db.session() as session:
        await session.execute(text("DELETE FROM procrastinate_jobs"))  # the job was lost
        await session.execute(
            update(Run)
            .where(Run.id == uuid.UUID(run["id"]))
            .values(status="running", heartbeat_at=func.now() - text("interval '10 minutes'"))
        )
        await session.commit()
    assert await queue.recover(app.state.db) == 1
    assert await _queued_jobs(app, run["id"]) == 1
    # Recovering again doesn't queue a duplicate.
    await queue.recover(app.state.db)
    assert await _queued_jobs(app, run["id"]) == 1


async def test_worker_executes_queued_runs(
    app: FastAPI, client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    """The real job queue path: the API queues, a worker picks the job up and runs it."""
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    queue: JobQueue = app.state.queue
    options = queue.worker_options({"executor": executor, "db": app.state.db})
    await queue.app.run_worker_async(
        **(options | {"wait": False, "install_signal_handlers": False, "queues": ["research"]})
    )
    assert (await _run(client, run["id"]))["status"] == "completed"
    async with app.state.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(SubQuestion)) == 2
        assert await session.scalar(select(func.count()).select_from(Finding)) == 2


async def test_event_stream_follows_a_live_run(
    app: FastAPI, client: AsyncClient, model: ScriptedModel, executor: RunExecutor
) -> None:
    project_id = await _project(client)
    run = await _start(client, project_id)
    await executor.execute(uuid.UUID(run["id"]))  # now waiting for approval
    await client.post(f"/api/v1/runs/{run['id']}/approve", json={})
    assert app.state.broker.healthy  # LISTEN is up, so streams wake on NOTIFY

    async def consume() -> list[str]:
        async with client.stream("GET", f"/api/v1/runs/{run['id']}/events/stream") as response:
            body = (await response.aread()).decode()
        return [line[7:] for line in body.splitlines() if line.startswith("event: ")]

    reader = asyncio.create_task(consume())
    await asyncio.sleep(0.2)
    assert not reader.done()  # still open: the run isn't finished
    await executor.execute(uuid.UUID(run["id"]))
    types = await asyncio.wait_for(reader, timeout=20)
    assert types[-2:] == ["run_completed", "end"] and "plan_ready" in types
