from fastapi.testclient import TestClient

from app.main import app
from app.services.model_client import OpenAICompatibleModel
from app.services.search_provider import TavilySearchProvider

client = TestClient(app)


def test_create_research_project() -> None:
    response = client.post(
        "/research/projects",
        json={
            "title": "AI startup market scan",
            "objective": "Analyze emerging AI startup builders in the developer tooling space.",
            "research_depth": "standard",
            "preferred_source_types": ["web", "docs"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "AI startup market scan"
    assert body["status"] == "created"
    assert body["objective"].startswith("Analyze")


def test_generate_research_plan() -> None:
    create_response = client.post(
        "/research/projects",
        json={
            "title": "LangGraph comparison",
            "objective": "Compare LangGraph and a direct orchestration approach for research tasks.",
            "research_depth": "deep",
            "preferred_source_types": ["web"],
        },
    )
    project_id = create_response.json()["id"]

    response = client.post(f"/research/projects/{project_id}/plan")

    assert response.status_code == 200
    body = response.json()
    assert body["objective"] == "Compare LangGraph and a direct orchestration approach for research tasks."
    assert len(body["tasks"]) >= 2
    assert body["tasks"][0]["title"]


def test_execute_research_workflow() -> None:
    create_response = client.post(
        "/research/projects",
        json={
            "title": "Open-source agent architecture",
            "objective": "Assess how open-source agent frameworks structure research workflows.",
            "research_depth": "standard",
            "preferred_source_types": ["web", "docs"],
        },
    )
    project_id = create_response.json()["id"]

    response = client.post(f"/research/projects/{project_id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["summary"]["status"] == "completed"
    assert body["sources"] == 0
    assert body["search_status"] == "not_configured"
    assert body["summary"]["research_status"] == "needs_search_configuration"


def test_execute_persists_search_sources(monkeypatch) -> None:
    monkeypatch.setattr(TavilySearchProvider, "__init__", lambda self: setattr(self, "api_key", "test-key"))
    monkeypatch.setattr(
        TavilySearchProvider,
        "search",
        lambda self, query, max_results=5: [
            {
                "title": "Research workflow design",
                "url": "https://example.test/research-workflow",
                "snippet": "A source excerpt for the workflow test.",
            }
        ],
    )
    monkeypatch.setattr(OpenAICompatibleModel, "generate", lambda self, prompt: "Finding [1] is supported by the retrieved source.")
    create_response = client.post(
        "/research/projects",
        json={
            "title": "Research workflow test",
            "objective": "Review research workflow design.",
            "preferred_source_types": ["web"],
        },
    )
    project_id = create_response.json()["id"]

    response = client.post(f"/research/projects/{project_id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["sources"] == 1
    assert body["model_status"] == "ready"
    assert "[1]" in body["report"]
    persisted = client.get(f"/research/projects/{project_id}/sources")
    assert persisted.status_code == 200
    assert persisted.json()[0]["url"] == "https://example.test/research-workflow"
