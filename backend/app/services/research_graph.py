from __future__ import annotations

import json
from typing import Any, TypedDict

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.services.model_client import OpenAICompatibleModel
from app.services.research_service import generate_research_plan
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
    sources: list[dict[str, str]]
    claims: list[dict[str, Any]]
    report: str
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


def plan_research(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    plan = generate_research_plan(
        project_id=state["project_id"],
        title=state["title"],
        objective=state["objective"],
        research_depth=state.get("research_depth", "standard"),
        preferred_sources=state.get("preferred_sources", []),
    )
    update = {"plan": plan.model_dump(mode="json")}
    _checkpoint(state, update, "plan", "search", config)
    return update


def collect_sources(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    provider = TavilySearchProvider()
    if not provider.api_key:
        update = {"sources": [], "search_status": "not_configured", "research_status": "needs_search_configuration"}
        _checkpoint(state, update, "search", "no_sources_report", config)
        return update

    try:
        sources = provider.search(state["objective"])
        search_status = "ready" if sources else "no_results"
    except httpx.HTTPError as exc:
        sources = []
        search_status = f"unavailable: {exc.__class__.__name__}"

    next_node = "extract" if sources else "no_sources_report"
    update = {
        "sources": sources,
        "search_status": search_status,
        "research_status": "evidence_available" if sources else "search_unavailable" if search_status.startswith("unavailable:") else "no_sources_found",
    }
    _checkpoint(state, update, "search", next_node, config)
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

    update = {"claims": claims, "extraction_status": extraction_status, "model_status": model_status}
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
    }
    _checkpoint(state, update, "verify", next_node, config)
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
    update = {"report": _build_report(state)}
    _checkpoint(state, update, "report", "__end__", config)
    return update


def create_insufficient_report(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    report = (
        f"Research objective: {state['objective']}\n\n"
        "No claims passed source-quote verification. No factual findings are reported. "
        "Review the collected sources or broaden the research question."
    )
    update = {"report": report}
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
    update = {"report": report}
    _checkpoint(state, update, "no_sources_report", "__end__", config)
    return update


def _start_node(state: ResearchState) -> str:
    return state.get("resume_from", "plan")


def _after_search(state: ResearchState) -> str:
    return "extract" if state.get("sources") else "no_sources_report"


def _after_verification(state: ResearchState) -> str:
    if any(claim.get("status") == "supported" for claim in state.get("claims", [])):
        return "report"
    return "insufficient_report"


def build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("plan", plan_research)
    graph.add_node("search", collect_sources)
    graph.add_node("extract", extract_claims)
    graph.add_node("verify", verify_claims)
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
            "report": "report",
            "insufficient_report": "insufficient_report",
            "no_sources_report": "no_sources_report",
        },
    )
    graph.add_edge("plan", "search")
    graph.add_conditional_edges("search", _after_search, {"extract": "extract", "no_sources_report": "no_sources_report"})
    graph.add_edge("extract", "verify")
    graph.add_conditional_edges(
        "verify",
        _after_verification,
        {"report": "report", "insufficient_report": "insufficient_report"},
    )
    graph.add_edge("report", END)
    graph.add_edge("insufficient_report", END)
    graph.add_edge("no_sources_report", END)
    return graph.compile()


research_graph = build_research_graph()
