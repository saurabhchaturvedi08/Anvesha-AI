from fastapi.testclient import TestClient

from app.routers import research as research_routes
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
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: '[{"statement":"The source describes a research workflow","quote":"A source excerpt for the workflow test.","source_index":1}]',
    )
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
    assert body["claims"][0]["status"] == "supported"
    assert "Research workflow design" in body["report"]
    persisted = client.get(f"/research/projects/{project_id}/sources")
    assert persisted.status_code == 200
    assert persisted.json()[0]["url"] == "https://example.test/research-workflow"
    claims = client.get(f"/research/projects/{project_id}/claims").json()
    evidence = client.get(f"/research/projects/{project_id}/evidence").json()
    assert claims[0]["status"] == "supported"
    assert evidence[0]["claim_id"] == claims[0]["id"]
    assert evidence[0]["source_id"] == persisted.json()[0]["id"]
    history = client.get(f"/research/projects/{project_id}/executions").json()
    assert history[0]["id"] == body["execution_id"]
    assert history[0]["checkpoint_count"] >= 5


def test_unsupported_claim_is_not_persisted_as_evidence(monkeypatch) -> None:
    monkeypatch.setattr(TavilySearchProvider, "__init__", lambda self: setattr(self, "api_key", "test-key"))
    monkeypatch.setattr(
        TavilySearchProvider,
        "search",
        lambda self, query, max_results=5: [
            {
                "title": "Evidence source",
                "url": "https://example.test/evidence",
                "snippet": "The source does not contain the claimed wording.",
            }
        ],
    )
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: '[{"statement":"Unsupported claim","quote":"This quote was invented.","source_index":1}]',
    )
    created = client.post(
        "/research/projects",
        json={"title": "Verification test", "objective": "Test evidence verification.", "preferred_source_types": ["web"]},
    )
    project_id = created.json()["id"]

    response = client.post(f"/research/projects/{project_id}/execute")

    assert response.status_code == 200
    assert response.json()["claims"][0]["status"] == "unsupported"
    assert response.json()["summary"]["verified_claim_count"] == 0
    assert client.get(f"/research/projects/{project_id}/evidence").json() == []
    assert "No claims passed source-quote verification" in response.json()["report"]


def test_failed_execution_resumes_from_saved_checkpoint(monkeypatch) -> None:
    created = client.post(
        "/research/projects",
        json={"title": "Resume test", "objective": "Verify execution recovery."},
    )
    project_id = created.json()["id"]
    original_invoke = research_routes.research_graph.invoke
    invocation_count = 0

    def fail_once(state, config=None):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            config["configurable"]["checkpoint_callback"](
                "verify",
                {
                    **state,
                    "plan": {"project_id": project_id, "title": "Resume test", "objective": "Verify execution recovery.", "tasks": []},
                    "sources": [
                        {
                            "title": "Recovery source",
                            "url": "https://example.test/recovery",
                            "snippet": "Recovery evidence is persisted before report generation.",
                        }
                    ],
                    "claims": [
                        {
                            "statement": "Recovery evidence is persisted before report generation.",
                            "quote": "Recovery evidence is persisted before report generation.",
                            "source_index": 1,
                            "status": "supported",
                            "source_url": "https://example.test/recovery",
                            "verification_note": "Exact quote match.",
                        }
                    ],
                    "search_status": "ready",
                    "model_status": "ready",
                    "research_status": "evidence_available",
                    "resume_from": "report",
                },
            )
            raise RuntimeError("temporary failure")
        return original_invoke(state, config=config)

    monkeypatch.setattr(research_routes.research_graph, "invoke", fail_once)
    failed = client.post(f"/research/projects/{project_id}/execute")
    assert failed.status_code == 500
    execution_id = failed.json()["detail"]["execution_id"]

    history = client.get(f"/research/projects/{project_id}/executions").json()
    assert history[0]["status"] == "failed"
    assert history[0]["current_node"] == "report"

    resumed = client.post(f"/research/projects/{project_id}/executions/{execution_id}/resume")

    assert resumed.status_code == 200
    assert resumed.json()["execution_id"] == execution_id
    assert resumed.json()["claims"][0]["status"] == "supported"
    assert invocation_count == 2


def test_document_ingestion_retrieval_and_follow_up(monkeypatch) -> None:
    project = client.post(
        "/research/projects",
        json={"title": "Local knowledge test", "objective": "Explore local retrieval."},
    ).json()
    uploaded = client.post(
        f"/research/projects/{project['id']}/documents",
        json={
            "filename": "notes.md",
            "content": "SQLite FTS5 provides local full-text retrieval. Documents remain inside the project workspace.",
        },
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["chunks_created"] >= 1

    retrieved = client.post(
        f"/research/projects/{project['id']}/retrieve",
        json={"query": "SQLite full-text retrieval"},
    )
    assert retrieved.status_code == 200
    assert retrieved.json()["results"][0]["filename"] == "notes.md"
    assert "SQLite" in retrieved.json()["results"][0]["text"]

    monkeypatch.setattr(OpenAICompatibleModel, "generate", lambda self, prompt: "SQLite supports local full-text retrieval [1].")
    follow_up = client.post(
        f"/research/projects/{project['id']}/follow-up",
        json={"query": "How does SQLite support local retrieval?"},
    )
    assert follow_up.status_code == 200
    assert "[1]" in follow_up.json()["answer"]
    assert follow_up.json()["sources"][0]["filename"] == "notes.md"

    duplicate = client.post(
        f"/research/projects/{project['id']}/documents",
        json={"filename": "copy.txt", "content": "SQLite FTS5 provides local full-text retrieval. Documents remain inside the project workspace."},
    )
    assert duplicate.status_code == 409

    unsupported = client.post(
        f"/research/projects/{project['id']}/documents",
        json={"filename": "notes.pdf", "content": "Unsupported file types are rejected."},
    )
    assert unsupported.status_code == 415

    other_project = client.post(
        "/research/projects",
        json={"title": "Isolation test", "objective": "Ensure project scoped retrieval."},
    ).json()
    client.post(
        f"/research/projects/{other_project['id']}/documents",
        json={"filename": "private.md", "content": "Only this project has the zephyrtopic keyword."},
    )
    isolated = client.post(
        f"/research/projects/{project['id']}/retrieve",
        json={"query": "zephyrtopic"},
    )
    assert isolated.status_code == 200
    assert isolated.json()["results"] == []
