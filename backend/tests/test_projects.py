import uuid

from fastapi import FastAPI
from httpx import AsyncClient

from anvesha.db.models import Project, Workspace

NEW_PROJECT = {"title": "  Solid-state batteries  ", "objective": "What limits commercial rollout?"}


async def test_create_and_get_project(client: AsyncClient) -> None:
    created = await client.post("/api/v1/projects", json=NEW_PROJECT)
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Solid-state batteries"  # whitespace stripped
    assert body["depth"] == "standard"
    assert body["created_at"] and body["updated_at"]

    fetched = await client.get(f"/api/v1/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


async def test_list_projects_newest_first(client: AsyncClient) -> None:
    for title in ("first", "second"):
        await client.post("/api/v1/projects", json={**NEW_PROJECT, "title": title})

    response = await client.get("/api/v1/projects")
    assert response.status_code == 200
    assert [project["title"] for project in response.json()] == ["second", "first"]


async def test_update_project_changes_only_sent_fields(client: AsyncClient) -> None:
    project = (await client.post("/api/v1/projects", json=NEW_PROJECT)).json()

    response = await client.patch(f"/api/v1/projects/{project['id']}", json={"depth": "deep"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["depth"] == "deep"
    assert updated["title"] == project["title"]
    assert updated["updated_at"] >= project["updated_at"]


async def test_update_rejects_null_fields(client: AsyncClient) -> None:
    project = (await client.post("/api/v1/projects", json=NEW_PROJECT)).json()
    response = await client.patch(f"/api/v1/projects/{project['id']}", json={"title": None})
    assert response.status_code == 422


async def test_delete_project(client: AsyncClient) -> None:
    project = (await client.post("/api/v1/projects", json=NEW_PROJECT)).json()

    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/projects/{project['id']}")).status_code == 404


async def test_create_validates_input(client: AsyncClient) -> None:
    blank = await client.post("/api/v1/projects", json={**NEW_PROJECT, "title": "   "})
    unknown_depth = await client.post("/api/v1/projects", json={**NEW_PROJECT, "depth": "max"})
    extra_field = await client.post("/api/v1/projects", json={**NEW_PROJECT, "owner": "x"})
    assert {blank.status_code, unknown_depth.status_code, extra_field.status_code} == {422}


async def test_unknown_project_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_projects_in_other_workspaces_are_invisible(
    app: FastAPI, client: AsyncClient
) -> None:
    async with app.state.db.session() as session:
        other = Workspace(name="Someone else")
        session.add(other)
        await session.flush()
        foreign = Project(workspace_id=other.id, title="Private", objective="Not yours")
        session.add(foreign)
        await session.commit()

    assert (await client.get(f"/api/v1/projects/{foreign.id}")).status_code == 404
    assert (
        await client.patch(f"/api/v1/projects/{foreign.id}", json={"depth": "deep"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/projects/{foreign.id}")).status_code == 404
    assert (await client.get("/api/v1/projects")).json() == []
