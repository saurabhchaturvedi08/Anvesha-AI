"""Project chat (Research Assistant), the clarifier, and the server config endpoint."""

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update

from anvesha.agents.assistant import strip_rendered_citations
from anvesha.chat import conversation_turns
from anvesha.config import Settings
from anvesha.db.models import (
    ChatMessage,
    Finding,
    ModelPreference,
    Report,
    Run,
    Source,
    UsageEvent,
)
from anvesha.llm.errors import LLMAuthError
from anvesha.llm.gateway import LLMGateway
from anvesha.llm.types import Completion, Message, ModelRoute, TokenUsage, ToolCall, ToolSpec
from tests.conftest import FakeWeb, _rebuild_source_service, make_settings

SOLAR_URL = "https://energy.example/solar"
WIND_URL = "https://energy.example/wind"
SOLAR_PAGE = """<html><head><title>Solar report</title></head><body><article>
<h1>Solar report</h1>
<p>Module prices fell for the third year in a row as factories expanded production.</p>
<p>Rooftop systems made up a growing share of new installations across Europe.</p>
</article></body></html>"""
WIND_PAGE = """<html><head><title>Wind report</title></head><body><article>
<h1>Wind report</h1>
<p>Wind power capacity grew by only 6 percent in 2025 because supply chains remained tight.</p>
</article></body></html>"""
SOLAR_QUOTE = "Module prices fell for the third year in a row as factories expanded production"
WIND_QUOTE = "Wind power capacity grew by only 6 percent in 2025"


class ScriptedAssistant:
    """Plays the assistant (and the clarifier), recognised by the tools it is offered."""

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.gate: asyncio.Event | None = None
        self.error: Exception | None = None
        self.answer = "Wind grew slowly [F1]. Module prices fell [F2]. Unknown [F9]."
        self.clarification: dict[str, Any] = {
            "ready": False,
            "questions": [
                {
                    "question": "Which region should the research cover?",
                    "why": "Growth differs a lot by region.",
                    "options": ["Global", "Europe", "Asia"],
                }
            ],
        }

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
        self.calls.append(list(messages))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        if "submit_clarification" in names:
            text, calls = "", [self._call("submit_clarification", **self.clarification)]
        else:
            text, calls = self._assistant(messages)
        return Completion(
            text=text,
            tool_calls=tuple(calls),
            usage=TokenUsage(input_tokens=500, output_tokens=100),
            provider=route.access.provider,
            model=route.model,
            finish_reason="tool_calls" if calls else "stop",
        )

    @staticmethod
    def _call(name: str, **arguments: Any) -> ToolCall:
        return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=arguments)

    def _assistant(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        last_question = max(i for i, m in enumerate(messages) if m.role == "user")
        while "My question:" not in messages[last_question].content:
            last_question -= 1
        tool_results = [m.content for m in messages[last_question:] if m.role == "tool"]
        if not tool_results:
            return "Let me look.", [self._call("search_sources", query="module prices")]
        if not any("Quote verified" in r for r in tool_results):
            source_id = re.search(r"source ([0-9a-f-]{36})", tool_results[0])
            assert source_id is not None, tool_results[0]
            return "", [self._call("cite", source_id=source_id.group(1), quote=SOLAR_QUOTE)]
        return self.answer, []


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return make_settings(database_url=database_url, openai_api_key="sk-deployment")


@pytest.fixture
async def assistant(app: FastAPI, fake_web: FakeWeb) -> ScriptedAssistant:
    fake_web.page(SOLAR_URL, SOLAR_PAGE)
    fake_web.page(WIND_URL, WIND_PAGE)
    scripted = ScriptedAssistant()
    app.state.gateway = LLMGateway(
        app.state.db, app.state.settings, app.state.vault, {"openai": scripted}
    )
    _rebuild_source_service(app)
    async with app.state.db.session() as session:
        session.add(
            ModelPreference(
                workspace_id=app.state.local_principal.workspace_id,
                role="lead",
                provider="openai",
                model="gpt-4o-mini",
            )
        )
        await session.commit()
    return scripted


async def _project_with_evidence(app: FastAPI, client: AsyncClient) -> str:
    """A project with two saved sources and a finished run whose report cites the wind one."""
    response = await client.post(
        "/api/v1/projects",
        json={"title": "Renewables", "objective": "How did renewables grow in 2025?"},
    )
    project_id = response.json()["id"]
    for url in (SOLAR_URL, WIND_URL):
        added = await client.post(f"/api/v1/projects/{project_id}/sources", json={"url": url})
        assert added.status_code == 201, added.text
    workspace_id = app.state.local_principal.workspace_id
    async with app.state.db.session() as session:
        wind = await session.scalar(select(Source).where(Source.url == WIND_URL))
        assert wind is not None
        run = Run(
            workspace_id=workspace_id,
            project_id=uuid.UUID(project_id),
            question="How did renewables grow in 2025?",
            depth="quick",
            status="completed",
        )
        session.add(run)
        await session.flush()
        session.add(
            Finding(
                workspace_id=workspace_id,
                project_id=run.project_id,
                run_id=run.id,
                source_id=wind.id,
                statement="Wind capacity grew 6% in 2025.",
                quote=WIND_QUOTE,
                quote_hash="h1",
                spans=[[0, len(WIND_QUOTE)]],
                status="verified",
            )
        )
        session.add(
            Report(
                workspace_id=workspace_id,
                project_id=run.project_id,
                run_id=run.id,
                version=1,
                status="complete",
                title="Renewables in 2025",
                markdown="# Renewables in 2025\n\nWind lagged [1].\n\n## Sources\n\n1. Wind report\n",
                citations=[],
            )
        )
        await session.commit()
    return str(project_id)


async def _new_chat(client: AsyncClient, project_id: str) -> str:
    response = await client.post(f"/api/v1/projects/{project_id}/chats", json={})
    assert response.status_code == 201, response.text
    assert response.json()["title"] == "New chat"
    return str(response.json()["id"])


async def _send(client: AsyncClient, thread_id: str, content: str) -> dict[str, Any]:
    response = await client.post(f"/api/v1/chats/{thread_id}/messages", json={"content": content})
    assert response.status_code == 202, response.text
    return dict(response.json())


async def test_assistant_answers_with_verified_citations(
    app: FastAPI, client: AsyncClient, assistant: ScriptedAssistant
) -> None:
    project_id = await _project_with_evidence(app, client)
    thread_id = await _new_chat(client, project_id)

    exchange = await _send(client, thread_id, "Why did wind grow slowly, and what about solar?")
    assert exchange["user_message"]["role"] == "user"
    assert exchange["assistant_message"]["status"] == "pending"
    await app.state.chat.wait_idle()

    chat = (await client.get(f"/api/v1/chats/{thread_id}")).json()
    assert chat["title"] == "Why did wind grow slowly, and what about solar?"
    question, reply = chat["messages"]
    assert question["content"].startswith("Why did wind")
    assert reply["status"] == "complete", reply
    # F1 (the run's wind finding) and F2 (a quote the assistant checked) are different
    # sources; the unknown F9 is dropped.
    assert reply["content"] == "Wind grew slowly [1]. Module prices fell [2]. Unknown."
    wind, solar = reply["citations"]
    assert wind["title"] == "Wind report" and wind["quotes"][0]["finding_id"] is not None
    assert solar["title"] == "Solar report" and solar["quotes"][0]["quote"] == SOLAR_QUOTE
    assert solar["quotes"][0]["finding_id"] is None
    assert reply["details"]["unknown_markers"] == ["F9"]
    assert [step["tool"] for step in reply["details"]["activity"]] == ["search_sources", "cite"]

    # The prompt carried the run's findings and the report, without its citation numbers.
    first_prompt = assistant.calls[0][-1].content
    assert "[F1] Wind capacity grew 6% in 2025." in first_prompt
    assert "Wind lagged." in first_prompt and "[1]" not in first_prompt

    async with app.state.db.session() as session:
        usage = (
            await session.scalars(
                select(UsageEvent).where(UsageEvent.project_id == uuid.UUID(project_id))
            )
        ).all()
    assert len(usage) == 3 and all(event.role == "lead" for event in usage)

    # The next question sees the conversation so far (citation numbers removed).
    assistant.calls.clear()
    await _send(client, thread_id, "Summarise that in one line.")
    await app.state.chat.wait_idle()
    history = assistant.calls[0]
    assert history[1].content.startswith("Why did wind")
    assert history[2].content == "Wind grew slowly. Module prices fell. Unknown."
    listed = (await client.get(f"/api/v1/projects/{project_id}/chats")).json()
    assert [item["id"] for item in listed] == [thread_id]


async def test_one_reply_at_a_time_and_streaming(
    app: FastAPI, client: AsyncClient, assistant: ScriptedAssistant
) -> None:
    project_id = await _project_with_evidence(app, client)
    thread_id = await _new_chat(client, project_id)
    assistant.gate = asyncio.Event()
    exchange = await _send(client, thread_id, "What changed in 2025?")
    reply_id = exchange["assistant_message"]["id"]

    busy = await client.post(f"/api/v1/chats/{thread_id}/messages", json={"content": "Again?"})
    assert busy.status_code == 409 and busy.json()["code"] == "reply_in_progress"

    async def consume() -> list[tuple[str, dict[str, Any]]]:
        events = []
        async with client.stream("GET", f"/api/v1/chat-messages/{reply_id}/stream") as stream:
            event_type = None
            async for line in stream.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: ") and event_type:
                    events.append((event_type, json.loads(line[6:])))
        return events

    reader = asyncio.create_task(consume())
    await asyncio.sleep(0.3)
    assistant.gate.set()
    events = await asyncio.wait_for(reader, timeout=15)
    types = [event_type for event_type, _ in events]
    assert types[0] == "progress" and types[-2:] == ["message", "end"]
    assert events[-2][1]["status"] == "complete" and events[-2][1]["citations"]
    assert any(
        len(data["activity"]) == 2 for event_type, data in events if event_type == "progress"
    )

    single = (await client.get(f"/api/v1/chat-messages/{reply_id}")).json()
    assert single["status"] == "complete"


async def test_failed_interrupted_and_abandoned_replies(
    app: FastAPI, client: AsyncClient, assistant: ScriptedAssistant
) -> None:
    project_id = await _project_with_evidence(app, client)
    thread_id = await _new_chat(client, project_id)

    assistant.error = LLMAuthError("The provider rejected the API key.")
    await _send(client, thread_id, "First question?")
    await app.state.chat.wait_idle()
    failed = (await client.get(f"/api/v1/chats/{thread_id}")).json()["messages"][-1]
    assert failed["status"] == "failed" and failed["error_code"] == "provider_auth_failed"

    # Shutting down while a reply is being written marks it failed.
    assistant.error = None
    assistant.gate = asyncio.Event()
    await _send(client, thread_id, "Second question?")
    await asyncio.sleep(0.2)
    await app.state.chat.aclose()
    interrupted = (await client.get(f"/api/v1/chats/{thread_id}")).json()["messages"][-1]
    assert interrupted["status"] == "failed" and interrupted["error_code"] == "interrupted"

    # A reply left pending by a process that died is reported as failed once it's stale.
    async with app.state.db.session() as session:
        stale = ChatMessage(
            workspace_id=app.state.local_principal.workspace_id,
            thread_id=uuid.UUID(thread_id),
            role="assistant",
            status="pending",
            content="",
        )
        session.add(stale)
        await session.flush()
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.id == stale.id)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()
        stale_id = stale.id
    message = (await client.get(f"/api/v1/chat-messages/{stale_id}")).json()
    assert message["status"] == "failed" and message["error_code"] == "interrupted"
    # ...and the thread accepts new messages again.
    assistant.gate = None
    await _send(client, thread_id, "Third question?")
    await app.state.chat.wait_idle()


async def test_chat_preconditions_and_management(
    app: FastAPI, client: AsyncClient, assistant: ScriptedAssistant
) -> None:
    project_id = await _project_with_evidence(app, client)
    thread_id = await _new_chat(client, project_id)

    renamed = await client.patch(f"/api/v1/chats/{thread_id}", json={"title": "Wind questions"})
    assert renamed.json()["title"] == "Wind questions"
    empty = await client.post(f"/api/v1/chats/{thread_id}/messages", json={"content": " "})
    assert empty.status_code == 422
    missing = await client.get(f"/api/v1/chats/{uuid.uuid4()}")
    assert missing.status_code == 404
    no_project = await client.post(f"/api/v1/projects/{uuid.uuid4()}/chats", json={})
    assert no_project.status_code == 404

    async with app.state.db.session() as session:
        preference = await session.get(
            ModelPreference, (app.state.local_principal.workspace_id, "lead")
        )
        await session.delete(preference)
        await session.commit()
    unconfigured = await client.post(
        f"/api/v1/chats/{thread_id}/messages", json={"content": "Hello?"}
    )
    assert unconfigured.status_code == 409
    assert unconfigured.json()["code"] == "model_not_configured"
    assert (await client.get(f"/api/v1/chats/{thread_id}")).json()["messages"] == []

    deleted = await client.delete(f"/api/v1/chats/{thread_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/chats/{thread_id}")).status_code == 404


async def test_clarify_and_server_config(
    app: FastAPI, client: AsyncClient, assistant: ScriptedAssistant
) -> None:
    response = await client.post("/api/v1/clarify", json={"question": "Is solar growing?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    assert body["questions"][0]["options"] == ["Global", "Europe", "Asia"]

    assistant.clarification = {"ready": True, "questions": body["questions"]}
    clear = (await client.post("/api/v1/clarify", json={"question": "Q?"})).json()
    assert clear == {"ready": True, "questions": []}

    config = (await client.get("/api/v1/config")).json()
    assert config["auth_mode"] == "local"
    assert config["depths"]["quick"]["max_rounds"] == 1
    assert config["run_default_max_cost_usd"] == "2.00"


async def test_projects_show_their_latest_run(
    app: FastAPI, client: AsyncClient, assistant: ScriptedAssistant
) -> None:
    project_id = await _project_with_evidence(app, client)
    other = (
        await client.post("/api/v1/projects", json={"title": "Empty", "objective": "Nothing yet"})
    ).json()
    assert other["latest_run"] is None
    listed = {p["id"]: p for p in (await client.get("/api/v1/projects")).json()}
    assert listed[project_id]["latest_run"]["status"] == "completed"
    assert listed[other["id"]]["latest_run"] is None
    single = (await client.get(f"/api/v1/projects/{project_id}")).json()
    assert single["latest_run"]["status"] == "completed"


def test_conversation_helpers() -> None:
    def message(role: str, content: str, status: str = "complete") -> ChatMessage:
        return ChatMessage(role=role, content=content, status=status)

    turns = conversation_turns(
        [
            message("user", "q1"),
            message("assistant", "a1"),
            message("user", "q2"),
            message("assistant", "", "failed"),
            message("user", "q3"),
            message("assistant", "a3"),
        ]
    )
    assert [(t.question, t.answer) for t in turns] == [("q1", "a1"), ("q3", "a3")]
    assert strip_rendered_citations("A [1]. B [2, 3].\n\n## Sources\n\n1. X") == "A. B."
