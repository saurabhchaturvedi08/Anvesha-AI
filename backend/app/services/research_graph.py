from __future__ import annotations

import json
from typing import Any, TypedDict

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.services.model_client import OpenAICompatibleModel
from app.services.research_service import generate_agentic_research_plan
from app.services.search_provider import TavilySearchProvider


class ResearchState(TypedDict, total=False):
    project_id: str
    execution_id: str
    title: str
    objective: str
    research_depth: str
    preferred_sources: list[str]
    resume_from: str
    plan: dict[str, Any]
    search_queries: list[str]
    completed_queries: list[str]
    research_round: int
    max_rounds: int
    sources: list[dict[str, str]]
    claims: list[dict[str, Any]]
    report: str
    agent_trace: list[dict[str, str]]
    planning_status: str
    reflection_status: str
    search_status: str
    model_status: str
    extraction_status: str
    research_status: str


def _checkpoint(
    state: ResearchState,
    update: dict[str, Any],
    node: str,
    resume_from: str,
    config: RunnableConfig,
) -> None:
    callback = config.get("configurable", {}).get("checkpoint_callback")
    if callable(callback):
        callback(node, {**state, **update, "resume_from": resume_from})


def _trace(state: ResearchState, agent: str, status: str, detail: str) -> list[dict[str, str]]:
    return [
        *state.get("agent_trace", []),
        {"agent": agent, "status": status, "detail": detail},
    ]


def plan_research(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    plan, planning_status = generate_agentic_research_plan(
        project_id=state["project_id"],
        title=state["title"],
        objective=state["objective"],
        research_depth=state.get("research_depth", "standard"),
        preferred_sources=state.get("preferred_sources", []),
    )
    queries = []
    for task in plan.tasks:
        query = task.search_query or f"{state['objective']} {task.title}"
        if query.casefold() not in {item.casefold() for item in queries}:
            queries.append(query)
    max_rounds = {"quick": 1, "standard": 2, "deep": 3}.get(state.get("research_depth", "standard"), 2)
    trace = _trace(
        state,
        "Planner",
        "ready" if planning_status == "ready" else "fallback",
        f"Created {len(plan.tasks)} research tasks using {plan.planning_mode.replace('_', ' ')} planning.",
    )
    update = {
        "plan": plan.model_dump(mode="json"),
        "planning_status": planning_status,
        "search_queries": queries[:3],
        "completed_queries": [],
        "research_round": 0,
        "max_rounds": max_rounds,
        "agent_trace": trace,
    }
    _checkpoint(state, update, "plan", "search", config)
    return update


def collect_sources(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    provider = TavilySearchProvider()
    if not provider.api_key:
        update = {
            "search_status": "not_configured",
            "research_status": "needs_search_configuration",
            "search_queries": [],
            "agent_trace": _trace(state, "Search agent", "not_configured", "Tavily API key is not configured."),
        }
        _checkpoint(state, update, "search", "no_sources_report", config)
        return update

    prior_sources = state.get("sources", [])
    sources = list(prior_sources)
    known_urls = {source.get("url", "").casefold() for source in sources}
    queries = state.get("search_queries", [])[:3]
    completed_queries = list(state.get("completed_queries", []))
    failures: list[str] = []
    for query in queries:
        try:
            results = provider.search(query, max_results=3)
            for source in results:
                url = source.get("url", "").casefold()
                if url and url not in known_urls:
                    sources.append(source)
                    known_urls.add(url)
        except httpx.HTTPError as exc:
            failures.append(exc.__class__.__name__)
        completed_queries.append(query)

    if sources:
        search_status = "ready"
    elif failures:
        search_status = f"unavailable: {failures[0]}"
    else:
        search_status = "no_results"

    round_number = state.get("research_round", 0) + (1 if queries else 0)
    update = {
        "sources": sources,
        "search_status": search_status,
        "completed_queries": completed_queries,
        "search_queries": [],
        "research_round": round_number,
        "research_status": "evidence_available" if sources else "search_unavailable" if search_status.startswith("unavailable:") else "no_sources_found",
        "agent_trace": _trace(
            state,
            "Search agent",
            "ready" if sources else "unavailable" if failures else "no_results",
            f"Round {round_number}: searched {len(queries)} query(s), collected {len(sources) - len(prior_sources)} new source(s).",
        ),
    }
    _checkpoint(state, update, "search", "extract" if sources else "no_sources_report", config)
    return update


def _decode_claims(response: str) -> list[dict[str, Any]]:
    content = response.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    payload = json.loads(content)
    if isinstance(payload, dict):
        payload = payload.get("claims", [])
    if not isinstance(payload, list):
        return []

    claims = []
    for item in payload[:12]:
        if not isinstance(item, dict):
            continue
        statement = item.get("statement")
        quote = item.get("quote")
        source_index = item.get("source_index")
        if (
            isinstance(statement, str)
            and statement.strip()
            and isinstance(quote, str)
            and quote.strip()
            and isinstance(source_index, int)
            and not isinstance(source_index, bool)
        ):
            claims.append(
                {
                    "statement": statement.strip()[:2000],
                    "quote": quote.strip()[:2000],
                    "source_index": source_index,
                }
            )
    return claims


def extract_claims(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    source_text = "\n".join(
        f"Source {index}: {source['title']}\n{source['snippet']}"
        for index, source in enumerate(state.get("sources", []), start=1)
    )
    prompt = (
        "Extract only concise factual claims directly stated in the source snippets. For every claim, "
        "provide the exact supporting quote and its 1-based source_index. Return only a JSON array with "
        "objects containing statement, quote, and source_index. Return [] if there are no supported claims. "
        "Do not infer or add outside facts.\n\n"
        f"Objective: {state['objective']}\n\n{source_text}"
    )
    try:
        claims = _decode_claims(OpenAICompatibleModel().generate(prompt))
        extraction_status = "ready"
        model_status = "ready"
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        claims = []
        extraction_status = f"unavailable: {exc.__class__.__name__}"
        model_status = extraction_status

    update = {
        "claims": claims,
        "extraction_status": extraction_status,
        "model_status": model_status,
        "agent_trace": _trace(
            state,
            "Extraction agent",
            "ready" if extraction_status == "ready" else "unavailable",
            f"Extracted {len(claims)} candidate claim(s) from {len(state.get('sources', []))} source(s).",
        ),
    }
    _checkpoint(state, update, "extract", "verify", config)
    return update


def verify_claims(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    sources = state.get("sources", [])
    verified = []
    for claim in state.get("claims", []):
        source_index = claim["source_index"]
        source = sources[source_index - 1] if 1 <= source_index <= len(sources) else None
        quote = claim["quote"]
        supported = bool(source and quote.casefold() in source.get("snippet", "").casefold())
        verified.append(
            {
                **claim,
                "status": "supported" if supported else "unsupported",
                "source_url": source["url"] if source and supported else None,
                "verification_note": (
                    "The quoted text was found in the cited source snippet."
                    if supported
                    else "The cited quote could not be matched to the specified source snippet."
                ),
            }
        )

    has_supported = any(claim["status"] == "supported" for claim in verified)
    next_node = "report" if has_supported else "insufficient_report"
    update = {
        "claims": verified,
        "research_status": "evidence_available" if has_supported else "insufficient_evidence",
        "agent_trace": _trace(
            state,
            "Verification agent",
            "ready",
            f"Verified {sum(claim['status'] == 'supported' for claim in verified)} of {len(verified)} candidate claim(s) against source text.",
        ),
    }
    _checkpoint(state, update, "verify", "reflect", config)
    return update


def reflect_on_evidence(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    supported = [claim for claim in state.get("claims", []) if claim.get("status") == "supported"]
    query = None
    reflection_status = "complete"
    if state.get("research_round", 0) < state.get("max_rounds", 1):
        verified_findings = "\n".join(f"- {claim['statement']}" for claim in supported) or "No claims passed verification."
        prompt = (
            "You are the research supervisor. Decide whether one more bounded web search would materially improve "
            "evidence for the objective. Return only JSON: {\"needs_more_research\": boolean, "
            "\"follow_up_query\": string or null}. Ask for more research when important aspects remain unanswered "
            "or no claims were verified. The query must target a specific evidence gap. Do not write findings.\n\n"
            f"Objective: {state['objective']}\n"
            f"Searches already completed: {json.dumps(state.get('completed_queries', []), ensure_ascii=False)}\n"
            f"Verified findings: {verified_findings}"
        )
        try:
            payload = json.loads(OpenAICompatibleModel().generate(prompt).strip())
            if not isinstance(payload, dict):
                raise ValueError("The supervisor returned an invalid response")
            proposed_query = payload.get("follow_up_query")
            if payload.get("needs_more_research") is True and isinstance(proposed_query, str) and proposed_query.strip():
                query = proposed_query.strip()[:500]
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            reflection_status = f"unavailable:{exc.__class__.__name__}"

    queries = [query] if query else []
    has_supported = bool(supported)
    next_node = "search" if queries else "report" if has_supported else "insufficient_report"
    update = {
        "search_queries": queries,
        "reflection_status": reflection_status,
        "research_status": "evidence_available" if has_supported else "insufficient_evidence",
        "agent_trace": _trace(
            state,
            "Supervisor",
            "more_research" if queries else "complete" if reflection_status == "complete" else "reflection_unavailable",
            f"Round {state.get('research_round', 0)} of {state.get('max_rounds', 1)}; "
            f"{'queued one focused follow-up search' if queries else 'no additional search was scheduled'}.",
        ),
    }
    _checkpoint(state, update, "reflect", next_node, config)
    return update


def _build_report(state: ResearchState) -> str:
    sources = state.get("sources", [])
    supported = [claim for claim in state.get("claims", []) if claim["status"] == "supported"]
    lines = [f"Research objective: {state['objective']}", "", "Verified findings:"]
    for claim in supported:
        source = sources[claim["source_index"] - 1]
        lines.extend(
            [
                f"- {claim['statement']} [{source['title']}]({source['url']})",
                f'  Supporting quote: "{claim["quote"]}"',
            ]
        )
    lines.extend(["", "Limitations: Findings are limited to the collected snippets; source quality and context require review."])
    return "\n".join(lines)


def create_report(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    update = {
        "report": _build_report(state),
        "agent_trace": _trace(state, "Report agent", "ready", "Assembled the report from verified claims and their cited sources."),
    }
    _checkpoint(state, update, "report", "__end__", config)
    return update


def create_insufficient_report(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    report = (
        f"Research objective: {state['objective']}\n\n"
        "No claims passed source-quote verification. No factual findings are reported. "
        "Review the collected sources or broaden the research question."
    )
    update = {
        "report": report,
        "agent_trace": _trace(state, "Report agent", "incomplete", "No claims passed source-quote verification."),
    }
    _checkpoint(state, update, "insufficient_report", "__end__", config)
    return update


def create_no_sources_report(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    search_status = state.get("search_status", "unknown")
    if search_status == "not_configured":
        reason = "No external sources were retrieved because Tavily is not configured. Configure search and retry."
    elif search_status.startswith("unavailable:"):
        reason = "The configured search provider was unavailable. Check connectivity and retry."
    else:
        reason = "The search provider returned no sources. Broaden the research question and retry."
    report = f"Research objective: {state['objective']}\n\nResearch is incomplete. {reason}"
    update = {
        "report": report,
        "agent_trace": _trace(state, "Report agent", "incomplete", reason),
    }
    _checkpoint(state, update, "no_sources_report", "__end__", config)
    return update


def _start_node(state: ResearchState) -> str:
    return state.get("resume_from", "plan")


def _after_search(state: ResearchState) -> str:
    return "extract" if state.get("sources") else "no_sources_report"


def _after_reflection(state: ResearchState) -> str:
    if state.get("search_queries"):
        return "search"
    if any(claim.get("status") == "supported" for claim in state.get("claims", [])):
        return "report"
    return "insufficient_report"


def build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("plan", plan_research)
    graph.add_node("search", collect_sources)
    graph.add_node("extract", extract_claims)
    graph.add_node("verify", verify_claims)
    graph.add_node("reflect", reflect_on_evidence)
    graph.add_node("report", create_report)
    graph.add_node("insufficient_report", create_insufficient_report)
    graph.add_node("no_sources_report", create_no_sources_report)
    graph.add_conditional_edges(
        START,
        _start_node,
        {
            "plan": "plan",
            "search": "search",
            "extract": "extract",
            "verify": "verify",
            "reflect": "reflect",
            "report": "report",
            "insufficient_report": "insufficient_report",
            "no_sources_report": "no_sources_report",
        },
    )
    graph.add_edge("plan", "search")
    graph.add_conditional_edges("search", _after_search, {"extract": "extract", "no_sources_report": "no_sources_report"})
    graph.add_edge("extract", "verify")
    graph.add_edge("verify", "reflect")
    graph.add_conditional_edges(
        "reflect",
        _after_reflection,
        {"search": "search", "report": "report", "insufficient_report": "insufficient_report"},
    )
    graph.add_edge("report", END)
    graph.add_edge("insufficient_report", END)
    graph.add_edge("no_sources_report", END)
    return graph.compile()


research_graph = build_research_graph()
