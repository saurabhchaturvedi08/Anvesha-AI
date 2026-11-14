import json

from fastapi.testclient import TestClient

from app.routers import research as research_routes
from app.main import app
from app.services.model_client import OpenAICompatibleModel
from app.services.research_graph import extract_entities_and_dates, filter_search_results, reflect_on_evidence
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


def test_generate_agentic_research_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: (
            '{"tasks":[{"title":"Map research landscape","description":"Find primary sources.",'
            '"search_query":"primary sources about research landscape","estimated_minutes":20}]}'
        ),
    )
    created = client.post(
        "/research/projects",
        json={"title": "Agentic planning", "objective": "Map the research landscape."},
    )

    response = client.post(f"/research/projects/{created.json()['id']}/plan")

    assert response.status_code == 200
    body = response.json()
    assert body["planning_mode"] == "agentic"
    assert body["tasks"][0]["search_query"] == "primary sources about research landscape"


def test_extraction_agent_returns_only_source_quoted_entities_and_dates(monkeypatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: json.dumps({
            "entities": [
                {
                    "value": "Acme Research",
                    "type": "organization",
                    "quote": "Acme Research launched the Atlas product in March 2025.",
                    "source_index": 1,
                },
                {
                    "value": "Invented Labs",
                    "type": "organization",
                    "quote": "Invented Labs launched a new product.",
                    "source_index": 1,
                },
            ],
            "dates": [
                {
                    "value": "March 2025",
                    "quote": "Acme Research launched the Atlas product in March 2025.",
                    "source_index": 1,
                },
            ],
        }),
    )
    state = {
        "objective": "Track organizations and launch dates.",
        "sources": [{
            "title": "Launch announcement",
            "url": "https://example.test/launch",
            "snippet": "Acme Research launched the Atlas product in March 2025.",
        }],
    }

    result = extract_entities_and_dates(state, {"configurable": {}})

    assert result["entity_extraction_status"] == "ready"
    assert result["extracted_entities"] == [{
        "value": "Acme Research",
        "quote": "Acme Research launched the Atlas product in March 2025.",
        "source_index": 1,
        "type": "organization",
    }]
    assert result["extracted_dates"] == [{
        "value": "March 2025",
        "quote": "Acme Research launched the Atlas product in March 2025.",
        "source_index": 1,
    }]


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
    assert body["search_agent_status"].startswith("fallback:")
    assert body["claims"][0]["status"] == "supported"
    assert body["verification_status"].startswith("fallback:")
    assert body["claims"][0]["verification_agent_verdict"] == "unavailable"
    assert "semantic AI review was unavailable" in body["claims"][0]["verification_note"]
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


def test_search_agent_filters_irrelevant_results(monkeypatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: json.dumps({
            "decisions": [
                {"source_index": 1, "relevant": False, "reason": "This result discusses gardening, not research workflows."},
                {"source_index": 2, "relevant": True, "reason": "This result directly discusses research workflow evidence."},
            ]
        }),
    )
    candidate_sources = [
        {
            "title": "Global gardening tips",
            "url": "https://example.test/gardening",
            "snippet": "Tips for growing tomatoes in small gardens.",
        },
        {
            "title": "Relevant technical reference",
            "url": "https://example.test/relevant",
            "snippet": "Research workflow systems use source-linked evidence.",
        },
    ]

    result = filter_search_results(
        {
            "objective": "Review research workflow evidence.",
            "plan": {"tasks": []},
            "candidate_sources": candidate_sources,
        },
        {},
    )

    assert result["search_agent_status"] == "ready"
    assert result["sources"] == [candidate_sources[1]]
    assert result["candidate_sources"] == []
    assert result["search_status"] == "ready"


def test_search_agent_reports_no_relevant_results(monkeypatch) -> None:
    monkeypatch.setattr(TavilySearchProvider, "__init__", lambda self: setattr(self, "api_key", "test-key"))
    monkeypatch.setattr(
        TavilySearchProvider,
        "search",
        lambda self, query, max_results=5: [{
            "title": "Unrelated gardening guide",
            "url": "https://example.test/gardening",
            "snippet": "Tips for growing tomatoes in small gardens.",
        }],
    )

    def fake_generate(self, prompt):
        if "research planning agent" in prompt:
            return '{"tasks":[{"title":"Research workflow","description":"Find workflow evidence.","search_query":"research workflow evidence","estimated_minutes":10}]}'
        if "You are the Search Agent." in prompt:
            return '{"decisions":[{"source_index":1,"relevant":false,"reason":"The result is about gardening, not research workflows."}]}'
        raise AssertionError("No downstream agent should run when every result is rejected")

    monkeypatch.setattr(OpenAICompatibleModel, "generate", fake_generate)
    created = client.post(
        "/research/projects",
        json={"title": "No relevant results", "objective": "Review research workflow evidence."},
    ).json()

    response = client.post(f"/research/projects/{created['id']}/execute")

    assert response.status_code == 200
    result = response.json()
    assert result["search_agent_status"] == "ready"
    assert result["search_status"] == "no_relevant_results"
    assert result["sources"] == 0
    assert "found no results relevant" in result["report"]


def test_supervisor_runs_bounded_follow_up_research(monkeypatch) -> None:
    search_queries: list[str] = []
    reflection_count = 0

    monkeypatch.setattr(TavilySearchProvider, "__init__", lambda self: setattr(self, "api_key", "test-key"))

    def fake_search(self, query, max_results=5):
        search_queries.append(query)
        if "follow-up detail" in query:
            return [{
                "title": "Follow-up source",
                "url": "https://example.test/follow-up",
                "snippet": "The follow-up source provides additional evidence for the research objective.",
            }]
        return [{
            "title": "Initial source",
            "url": "https://example.test/initial",
            "snippet": "The initial source supports the research objective.",
        }]

    def fake_generate(self, prompt):
        nonlocal reflection_count
        if "research planning agent" in prompt:
            return (
                '{"tasks":[{"title":"Gather initial evidence","description":"Search primary sources.",'
                '"search_query":"initial evidence for the objective","estimated_minutes":15}]}'
            )
        if "You are the Search Agent." in prompt:
            return json.dumps({
                "decisions": [
                    {"source_index": 1, "relevant": True, "reason": "Relevant to the research objective."},
                    *([{"source_index": 2, "relevant": True, "reason": "Relevant follow-up source."}] if '"source_index": 2' in prompt else []),
                ]
            })
        if "You are the research agent." in prompt:
            has_follow_up = '"source_index": 2' in prompt
            return json.dumps({
                "task_results": [{
                    "task_id": "step-1",
                    "status": "covered" if has_follow_up else "partial",
                    "findings": [
                        {
                            "statement": "Initial evidence supports the research objective.",
                            "quote": "The initial source supports the research objective.",
                            "source_index": 1,
                        },
                        *([{
                            "statement": "Follow-up evidence adds detail.",
                            "quote": "The follow-up source provides additional evidence for the research objective.",
                            "source_index": 2,
                        }] if has_follow_up else []),
                    ],
                }]
            })
        if "You are the Extraction Agent." in prompt:
            return '{"entities":[],"dates":[]}'
        if "research analysis agent" in prompt:
            return '{"observations":[{"statement":"The two findings address related aspects.","relation":"related","claim_indices":[1,2]}]}'
        if "report-organization agent" in prompt:
            return '{"sections":[{"title":"Verified findings","claim_indices":[1,2]}]}'
        if "You are the Verification Agent." in prompt:
            reviews = [{"claim_index": 1, "verdict": "supports", "reason": "The excerpt directly states this."}]
            if '"claim_index": 2' in prompt:
                reviews.append({"claim_index": 2, "verdict": "supports", "reason": "The excerpt directly states this."})
            return json.dumps({"reviews": reviews})
        if "Extract only concise factual claims" in prompt:
            claims = [{
                "statement": "Initial evidence supports the objective.",
                "quote": "The initial source supports the research objective.",
                "source_index": 1,
            }]
            if "Source 2:" in prompt:
                claims.append({
                    "statement": "Follow-up evidence adds detail.",
                    "quote": "The follow-up source provides additional evidence for the research objective.",
                    "source_index": 2,
                })
            return json.dumps(claims)
        if "research supervisor" in prompt:
            reflection_count += 1
            if reflection_count == 1:
                return '{"needs_more_research":true,"follow_up_query":"follow-up detail for the objective"}'
            return '{"needs_more_research":false,"follow_up_query":null}'
        raise AssertionError("Unexpected model agent prompt")

    monkeypatch.setattr(TavilySearchProvider, "search", fake_search)
    monkeypatch.setattr(OpenAICompatibleModel, "generate", fake_generate)
    created = client.post(
        "/research/projects",
        json={
            "title": "Bounded agent loop",
            "objective": "Gather and verify evidence for the objective.",
            "research_depth": "standard",
        },
    )

    response = client.post(f"/research/projects/{created.json()['id']}/execute")

    assert response.status_code == 200
    body = response.json()
    assert search_queries == ["initial evidence for the objective", "follow-up detail for the objective"]
    assert body["sources"] == 2
    assert body["summary"]["verified_claim_count"] == 2
    assert body["task_coverage"][0]["finding_count"] == 2
    assert all(finding["quote"] for finding in body["research_findings"])
    assert body["analysis"][0]["relation"] == "related"
    assert any(step["agent"] == "Supervisor" and step["status"] == "more_research" for step in body["agent_trace"])
    assert any(step["agent"] == "Research agent" for step in body["agent_trace"])
    assert body["verification_status"] == "ready"
    assert all(claim["verification_agent_verdict"] == "supports" for claim in body["claims"])
    assert any(step["agent"] == "Verification Agent" for step in body["agent_trace"])
    assert any(step["agent"] == "Analysis agent" for step in body["agent_trace"])
    assert any(step["agent"] == "Report agent" for step in body["agent_trace"])
    assert body["supervisor_status"] == "complete"


def test_supervisor_does_not_repeat_completed_query(monkeypatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: '{"needs_more_research":true,"follow_up_query":"  INITIAL   evidence query  "}',
    )
    result = reflect_on_evidence(
        {
            "objective": "Gather and verify evidence.",
            "research_round": 1,
            "max_rounds": 2,
            "completed_queries": ["initial evidence query"],
            "claims": [{"statement": "A verified finding.", "status": "supported"}],
        },
        {},
    )

    assert result["search_queries"] == []
    assert result["supervisor_status"] == "fallback:ValueError"
    assert result["agent_trace"][-1]["status"] == "fallback"


def test_supervisor_rejects_malformed_decision(monkeypatch) -> None:
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: '{"needs_more_research":"yes","follow_up_query":"find more"}',
    )
    result = reflect_on_evidence(
        {
            "objective": "Gather and verify evidence.",
            "research_round": 1,
            "max_rounds": 2,
            "claims": [],
        },
        {},
    )

    assert result["search_queries"] == []
    assert result["supervisor_status"] == "fallback:ValueError"
    assert result["research_status"] == "insufficient_evidence"


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


def test_verification_agent_rejects_overclaim_despite_exact_quote(monkeypatch) -> None:
    monkeypatch.setattr(TavilySearchProvider, "__init__", lambda self: setattr(self, "api_key", "test-key"))
    monkeypatch.setattr(
        TavilySearchProvider,
        "search",
        lambda self, query, max_results=5: [{
            "title": "Quarterly filing",
            "url": "https://example.test/filing",
            "snippet": "The company reported revenue rose 5% in the first quarter.",
        }],
    )

    def fake_generate(self, prompt):
        if "research planning agent" in prompt:
            return '{"tasks":[{"title":"Review filing","description":"Find reported results.","search_query":"quarterly filing","estimated_minutes":10}]}'
        if "You are the Search Agent." in prompt:
            return '{"decisions":[{"source_index":1,"relevant":true,"reason":"The filing reports company revenue results."}]}'
        if "You are the research agent." in prompt:
            return json.dumps({
                "task_results": [{
                    "task_id": "step-1",
                    "status": "partial",
                    "findings": [{
                        "statement": "Revenue doubled every quarter.",
                        "quote": "The company reported revenue rose 5% in the first quarter.",
                        "source_index": 1,
                    }],
                }]
            })
        if "You are the Extraction Agent." in prompt:
            return '{"entities":[],"dates":[]}'
        if "Extract only concise factual claims" in prompt:
            return json.dumps([{
                "statement": "Revenue doubled every quarter.",
                "quote": "The company reported revenue rose 5% in the first quarter.",
                "source_index": 1,
            }])
        if "You are the Verification Agent." in prompt:
            return json.dumps({
                "reviews": [{
                    "claim_index": 1,
                    "verdict": "contradicts",
                    "reason": "A 5% increase does not mean revenue doubled every quarter.",
                }]
            })
        if "You are the research supervisor." in prompt:
            return '{"needs_more_research":false,"follow_up_query":null}'
        raise AssertionError("Unexpected model agent prompt")

    monkeypatch.setattr(OpenAICompatibleModel, "generate", fake_generate)
    created = client.post(
        "/research/projects",
        json={"title": "Overclaim review", "objective": "Check the reported revenue change."},
    ).json()

    response = client.post(f"/research/projects/{created['id']}/execute")

    assert response.status_code == 200
    result = response.json()
    assert result["claims"][0]["status"] == "unsupported"
    assert result["claims"][0]["verification_agent_verdict"] == "contradicts"
    assert client.get(f"/research/projects/{created['id']}/evidence").json() == []


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

    invalid_quote = False
    semantic_reject = False

    def fake_generate(self, prompt):
        nonlocal invalid_quote, semantic_reject
        if "local-document research planner" in prompt:
            return '{"queries":["SQLite full-text retrieval"]}'
        if "You are the research agent." in prompt:
            return '{"task_results":[]}'
        if "research supervisor for local-document search" in prompt:
            return '{"needs_more_research":false,"follow_up_query":null}'
        if "Answer the question using only the supplied local-document excerpts" in prompt:
            if invalid_quote:
                return json.dumps([{
                    "statement": "A fabricated document claim.",
                    "quote": "This quote is absent from every document.",
                    "source_index": 1,
                }])
            return json.dumps([{
                "statement": "SQLite provides local full-text retrieval.",
                "quote": "SQLite FTS5 provides local full-text retrieval.",
                "source_index": 1,
            }])
        if "You are the Verification Agent for local-document answers." in prompt:
            verdict = "contradicts" if semantic_reject else "supports"
            return json.dumps({
                "reviews": [{
                    "claim_index": 1,
                    "verdict": verdict,
                    "reason": "The excerpt does not support this conclusion." if semantic_reject else "The excerpt directly states this.",
                }]
            })
        raise AssertionError("Unexpected follow-up agent prompt")

    monkeypatch.setattr(OpenAICompatibleModel, "generate", fake_generate)
    follow_up = client.post(
        f"/research/projects/{project['id']}/follow-up",
        json={"query": "How does SQLite support local retrieval?"},
    )
    assert follow_up.status_code == 200
    assert "[1]" in follow_up.json()["answer"]
    assert follow_up.json()["sources"][0]["filename"] == "notes.md"
    assert [step["agent"] for step in follow_up.json()["agent_trace"]] == [
        "Document planner",
        "Retrieval agent",
        "Document supervisor",
        "Document answer agent",
        "Verification Agent",
    ]
    assert follow_up.json()["claims"][0]["verification_agent_verdict"] == "supports"

    invalid_quote = True
    unverified_follow_up = client.post(
        f"/research/projects/{project['id']}/follow-up",
        json={"query": "What does this document say?"},
    )
    assert unverified_follow_up.status_code == 200
    assert unverified_follow_up.json()["claims"] == []
    assert "quote and semantic verification" in unverified_follow_up.json()["answer"]

    invalid_quote = False
    semantic_reject = True
    rejected_follow_up = client.post(
        f"/research/projects/{project['id']}/follow-up",
        json={"query": "What does this document say?"},
    )
    assert rejected_follow_up.status_code == 200
    assert rejected_follow_up.json()["claims"][0]["status"] == "unsupported"
    assert rejected_follow_up.json()["claims"][0]["verification_agent_verdict"] == "contradicts"
    assert rejected_follow_up.json()["answer"].startswith("The retrieved excerpts did not contain")

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


def test_execution_records_budget_status_and_job_metadata(monkeypatch) -> None:
    monkeypatch.setattr(TavilySearchProvider, "__init__", lambda self: setattr(self, "api_key", "test-key"))
    monkeypatch.setattr(
        TavilySearchProvider,
        "search",
        lambda self, query, max_results=5: [
            {
                "title": "Budgeted source",
                "url": "https://example.test/budgeted",
                "snippet": "The budgeted source states the planned run is subject to an explicit limit.",
            },
            {
                "title": "Second budgeted source",
                "url": "https://example.test/budgeted-2",
                "snippet": "The second source confirms the same budgeted data point.",
            },
        ],
    )
    monkeypatch.setattr(
        OpenAICompatibleModel,
        "generate",
        lambda self, prompt: '[{"statement":"A budgeted claim exists.","quote":"The budgeted source states the planned run is subject to an explicit limit.","source_index":1},{"statement":"The second claim confirms the same point.","quote":"The second source confirms the same budgeted data point.","source_index":2}]',
    )

    created = client.post(
        "/research/projects",
        json={"title": "Budget guard", "objective": "Ensure production execution tracks budget ceilings.", "preferred_source_types": ["web"]},
    )
    project_id = created.json()["id"]

    response = client.post(f"/research/projects/{project_id}/execute", json={"budget_limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["budget_status"] == "exceeded"
    assert body["summary"]["budget_limit"] == 1

    history = client.get(f"/research/projects/{project_id}/executions").json()
    assert history[0]["job_status"] == "budget_exceeded"
    assert history[0]["budget_limit"] == 1
