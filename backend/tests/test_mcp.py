"""The MCP server, end to end: access tokens, the /mcp endpoint, and every tool, driven
through the official MCP client against the real app."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import mcp_types as types
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text, update

from anvesha.access_tokens import create_access_token, hash_token, verify_access_token
from anvesha.config import Settings
from anvesha.db.models import AccessToken
from anvesha.graph.executor import RunExecutor
from anvesha.tenancy import Principal
from tests.conftest import FakeWeb, make_settings
from tests.test_runs import (  # noqa: F401 (pytest fixtures)
    SOLAR_URL,
    ScriptedModel,
    executor,
    model,
)

PAGE_URL = "https://news.example/heat"
PAGE = """<html><head><title>Heatwave study</title></head><body><article>
<p>The 2024 heatwave raised hospital admissions by 18 percent across the region, the study found.</p>
<p>Researchers compared admissions data from 42 hospitals over three summers.</p>
</article></body></html>"""
RESEARCH_TOOLS = {
    "start_research",
    "get_research_status",
    "approve_plan",
    "cancel_research",
    "get_report",
    "search_evidence",
    "list_projects",
    "create_project",
}
RAW_TOOLS = {
    "web_search",
    "scholar_search",
    "fetch_page",
    "read_source",
    "search_sources",
    "verify_quote",
    "list_sources",
}


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    # A deployment key, so the scripted model's "openai" routes resolve.
    return make_settings(database_url=database_url, openai_api_key="sk-deployment")


async def _token(client: AsyncClient, name: str = "test client", **body: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/access-tokens", json={"name": name, **body})
    assert response.status_code == 201, response.text
    return dict(response.json())


@asynccontextmanager
async def _mcp(app: FastAPI, token: str) -> AsyncIterator[ClientSession]:
    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ) as http,
        streamable_http_client("http://test/mcp", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def _call(session: ClientSession, name: str, **arguments: Any) -> types.CallToolResult:
    return await session.call_tool(name, arguments)


def _text(result: types.CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, types.TextContent))


def _data(result: types.CallToolResult) -> dict[str, Any]:
    assert isinstance(result.structured_content, dict), _text(result)
    return result.structured_content


# --- Access tokens ----------------------------------------------------------------------


async def test_access_tokens_are_shown_once_and_can_be_revoked(
    app: FastAPI, client: AsyncClient
) -> None:
    created = await _token(client, "Claude Code", expires_in_days=30)
    token = created["token"]
    assert token.startswith("anv_") and len(token) > 40
    assert created["prefix"] == token[:8] and created["expires_at"] is not None

    listed = (await client.get("/api/v1/access-tokens")).json()
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0] and listed[0]["last_used_at"] is None

    # Only the hash is stored.
    async with app.state.db.session() as session:
        row = await session.get(AccessToken, uuid.UUID(created["id"]))
        assert row is not None and row.token_hash == hash_token(token)
        assert token not in (row.token_hash, row.prefix)

    verified = await verify_access_token(app.state.db, token)
    assert verified is not None and verified.principal == app.state.local_principal
    listed = (await client.get("/api/v1/access-tokens")).json()
    assert listed[0]["last_used_at"] is not None

    assert (await client.delete(f"/api/v1/access-tokens/{created['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/access-tokens/{created['id']}")).status_code == 404
    assert await verify_access_token(app.state.db, token) is None
    assert (await client.get("/api/v1/access-tokens")).json() == []

    bad = await client.post("/api/v1/access-tokens", json={"name": " ", "extra": 1})
    assert bad.status_code == 422


async def test_mcp_endpoint_refuses_missing_wrong_and_expired_tokens(
    app: FastAPI, client: AsyncClient
) -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}

    missing = await client.post("/mcp", json=initialize, headers=headers)
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Bearer ")

    for bad in ("anv_not-a-real-token", "sk-something-else", "anv_" + "x" * 300):
        wrong = await client.post(
            "/mcp", json=initialize, headers={**headers, "Authorization": f"Bearer {bad}"}
        )
        assert wrong.status_code == 401, bad

    expired = await _token(client, "old")
    async with app.state.db.session() as session:
        await session.execute(
            update(AccessToken).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    refused = await client.post(
        "/mcp",
        json=initialize,
        headers={**headers, "Authorization": f"Bearer {expired['token']}"},
    )
    assert refused.status_code == 401

    valid = await _token(client)
    accepted = await client.post(
        "/mcp", json=initialize, headers={**headers, "Authorization": f"Bearer {valid['token']}"}
    )
    assert accepted.status_code == 200, accepted.text


async def test_mcp_can_be_turned_off(database_url: str) -> None:
    from anvesha.api.app import create_app

    application = create_app(make_settings(database_url=database_url, mcp_enabled=False))
    assert "/mcp" not in {getattr(route, "path", None) for route in application.routes}


# --- Tools ------------------------------------------------------------------------------


async def test_tools_are_listed_with_schemas_and_hints(app: FastAPI, client: AsyncClient) -> None:
    token = (await _token(client))["token"]
    async with _mcp(app, token) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    assert set(tools) == RESEARCH_TOOLS | RAW_TOOLS
    for tool in tools.values():
        assert tool.input_schema["type"] == "object" and tool.description
        assert tool.annotations is not None
    for name in RAW_TOOLS:
        schema = tools[name].input_schema
        assert schema["required"][0] == "project_id" and "project_id" in schema["properties"]
    assert tools["fetch_page"].input_schema["required"] == ["project_id", "url"]
    assert tools["get_report"].annotations.read_only_hint is True  # type: ignore[union-attr]
    assert tools["cancel_research"].annotations.destructive_hint is True  # type: ignore[union-attr]
    assert tools["web_search"].annotations.open_world_hint is True  # type: ignore[union-attr]
    assert tools["start_research"].annotations.read_only_hint is False  # type: ignore[union-attr]


async def test_researching_with_raw_tools(
    app: FastAPI, client: AsyncClient, fake_web: FakeWeb
) -> None:
    token = (await _token(client))["token"]
    fake_web.page(PAGE_URL, PAGE)
    async with _mcp(app, token) as session:
        created = await _call(
            session, "create_project", title="Heat", objective="How do heatwaves affect health?"
        )
        assert not created.is_error
        project_id = _data(created)["project_id"]

        fetched = await _call(session, "fetch_page", project_id=project_id, url=PAGE_URL)
        assert not fetched.is_error, _text(fetched)
        source_id = _data(fetched)["source_id"]
        assert "<untrusted_content" in _text(fetched)

        verified = await _call(
            session,
            "verify_quote",
            project_id=project_id,
            source_id=source_id,
            quote="raised hospital admissions by 18 percent across the region",
        )
        assert _data(verified)["supported"] is True and _text(verified).startswith("VERIFIED")

        listed = await _call(session, "list_sources", project_id=project_id)
        assert source_id in _text(listed)

        projects = await _call(session, "list_projects")
        assert [p["project_id"] for p in _data(projects)["projects"]] == [project_id]

        # Failures come back as error results the model can read, not protocol errors.
        unknown_project = await _call(
            session, "list_sources", project_id="00000000-0000-0000-0000-000000000000"
        )
        assert unknown_project.is_error and "Project not found" in _text(unknown_project)
        missing_project = await _call(session, "list_sources")
        assert missing_project.is_error and "project_id" in _text(missing_project)
        bad_args = await _call(
            session, "read_source", project_id=project_id, source_id="nope", extra=1
        )
        assert bad_args.is_error and "invalid_arguments" in _text(bad_args)
        no_tool = await _call(session, "delete_everything")
        assert no_tool.is_error and "no tool named" in _text(no_tool)

    # Sources fetched over MCP were chosen by an AI agent, so they are marked as such.
    sources = (await client.get(f"/api/v1/projects/{project_id}/sources")).json()
    assert [s["added_by"] for s in sources] == ["agent"]


async def test_tokens_only_reach_their_own_workspace(
    app: FastAPI, client: AsyncClient, fake_web: FakeWeb
) -> None:
    mine = (await _token(client))["token"]
    async with _mcp(app, mine) as session:
        project_id = _data(
            await _call(session, "create_project", title="Mine", objective="Private question")
        )["project_id"]

    async with app.state.db.session() as session:
        other_user, other_workspace = uuid.uuid4(), uuid.uuid4()
        await session.execute(
            text("INSERT INTO users (id, email) VALUES (:id, 'other@example.org')"),
            {"id": other_user},
        )
        await session.execute(
            text("INSERT INTO workspaces (id, name) VALUES (:id, 'Other')"),
            {"id": other_workspace},
        )
        await session.execute(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES (:workspace, :user, 'owner')"
            ),
            {"workspace": other_workspace, "user": other_user},
        )
        await session.commit()
        _, theirs = await create_access_token(
            session, Principal(user_id=other_user, workspace_id=other_workspace), name="theirs"
        )

    async with _mcp(app, theirs) as session:
        assert _data(await _call(session, "list_projects")) == {"projects": []}
        for name, arguments in (
            ("list_sources", {"project_id": project_id}),
            ("search_evidence", {"project_id": project_id}),
            ("get_report", {"project_id": project_id}),
            ("start_research", {"project_id": project_id, "question": "Steal it"}),
        ):
            result = await _call(session, name, **arguments)
            assert result.is_error, name

    # And their tokens don't show up in this user's list.
    assert (await client.get("/api/v1/access-tokens")).json()[0]["name"] == "test client"
    assert len((await client.get("/api/v1/access-tokens")).json()) == 1


async def test_research_lifecycle_over_mcp(
    app: FastAPI,
    client: AsyncClient,
    model: ScriptedModel,  # noqa: F811 (fixture from test_runs)
    executor: RunExecutor,  # noqa: F811
) -> None:
    token = (await _token(client))["token"]
    async with _mcp(app, token) as session:
        started = await _call(
            session,
            "start_research",
            question="How did renewables grow in 2025?",
            depth="quick",
            autopilot=False,
            max_cost_usd="1.50",
        )
        assert not started.is_error, _text(started)
        run = _data(started)
        assert run["status"] == "queued" and Decimal(run["max_cost_usd"]) == Decimal("1.5")
        assert "approve_plan" in _text(started)
        run_id, project_id = run["run_id"], run["project_id"]

        # The project was created from the question.
        project = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert project["objective"] == "How did renewables grow in 2025?"

        not_yet = await _call(session, "get_report", run_id=run_id)
        assert not_yet.is_error and "no report yet" in _text(not_yet)

        await executor.execute(uuid.UUID(run_id))
        paused = await _call(session, "get_research_status", run_id=run_id)
        assert _data(paused)["status"] == "awaiting_approval"
        assert "How fast did solar capacity grow in 2025?" in _text(paused)
        assert "Recent activity" in _text(paused) and "approve_plan" in _text(paused)

        approved = await _call(
            session,
            "approve_plan",
            run_id=run_id,
            sub_questions=[{"question": "How fast did solar capacity grow in 2025?"}],
        )
        assert _data(approved)["status"] == "queued"
        await executor.execute(uuid.UUID(run_id))

        done = await _call(session, "get_research_status", run_id=run_id, recent_events=0)
        status = _data(done)
        assert status["status"] == "completed" and status["findings"] == {"verified": 1}
        assert status["report_id"] and "get_report" in _text(done)
        assert "Recent activity" not in _text(done)

        report = await _call(session, "get_report", run_id=run_id, include_quotes=True)
        assert not report.is_error
        assert _data(report)["report_id"] == status["report_id"]
        assert [c["url"] for c in _data(report)["citations"]] == [SOLAR_URL]
        assert "Global solar capacity grew by 24 percent in 2025" in _text(report)
        latest = await _call(session, "get_report", project_id=project_id)
        assert _data(latest)["report_id"] == status["report_id"]
        both = await _call(session, "get_report", run_id=run_id, project_id=project_id)
        assert both.is_error

        hits = await _call(session, "search_evidence", project_id=project_id, query="solar growth")
        findings = _data(hits)["findings"]
        assert len(findings) == 1 and findings[0]["source_url"] == SOLAR_URL
        assert findings[0]["status"] == "verified"
        nothing = await _call(session, "search_evidence", project_id=project_id, query="volcano")
        assert _data(nothing)["findings"] == []
        recent = await _call(session, "search_evidence", project_id=project_id)
        assert len(_data(recent)["findings"]) == 1

        finished = await _call(session, "cancel_research", run_id=run_id)
        assert finished.is_error and "already completed" in _text(finished)

        # A second run in the same project, cancelled while queued.
        second = _data(
            await _call(session, "start_research", question="And in 2026?", project_id=project_id)
        )
        assert second["project_id"] == project_id and second["autopilot"] is True
        busy = await _call(session, "start_research", question="Third?", project_id=project_id)
        assert busy.is_error and "run_in_progress" in _text(busy)
        cancelled = await _call(session, "cancel_research", run_id=second["run_id"])
        assert _data(cancelled)["status"] == "cancelled"


async def test_start_research_without_models_creates_nothing(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.settings = make_settings(database_url=app.state.settings.database_url)
    token = (await _token(client))["token"]
    async with _mcp(app, token) as session:
        result = await _call(session, "start_research", question="Anything at all?")
        assert result.is_error and "model" in _text(result).lower()
        assert _data(await _call(session, "list_projects")) == {"projects": []}
        too_much = await _call(session, "start_research", question="Expensive?", max_cost_usd="-1")
        assert too_much.is_error and "invalid_arguments" in _text(too_much)
