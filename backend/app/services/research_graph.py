from __future__ import annotations

from typing import Any, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from app.services.model_client import OpenAICompatibleModel
from app.services.research_service import generate_research_plan
from app.services.search_provider import TavilySearchProvider


class ResearchState(TypedDict, total=False):
    project_id: str
    title: str
    objective: str
    research_depth: str
    preferred_sources: list[str]
    plan: dict[str, Any]
    sources: list[dict[str, str]]
    report: str
    search_status: str
    model_status: str


def plan_research(state: ResearchState) -> dict[str, Any]:
    plan = generate_research_plan(
        project_id=state["project_id"],
        title=state["title"],
        objective=state["objective"],
        research_depth=state.get("research_depth", "standard"),
        preferred_sources=state.get("preferred_sources", []),
    )
    return {"plan": plan.model_dump(mode="json")}


def collect_sources(state: ResearchState) -> dict[str, Any]:
    provider = TavilySearchProvider()
    if not provider.api_key:
        return {"sources": [], "search_status": "not_configured"}

    try:
        sources = provider.search(state["objective"])
    except httpx.HTTPError as exc:
        return {"sources": [], "search_status": f"unavailable: {exc.__class__.__name__}"}
    return {"sources": sources, "search_status": "ready"}


def synthesize_report(state: ResearchState) -> dict[str, Any]:
    sources = state.get("sources", [])
    if sources:
        source_text = "\n".join(
            f"[{index}] {source['title']} ({source['url']}): {source['snippet']}"
            for index, source in enumerate(sources, start=1)
        )
        prompt = (
            "Write a concise research brief for the objective below using only the supplied sources. "
            "Cite factual statements with their source numbers, distinguish evidence from uncertainty, "
            "and include limitations. Do not invent facts.\n\n"
            f"Objective: {state['objective']}\n\nSources:\n{source_text}"
        )
    else:
        prompt = (
            "No external search sources were retrieved. Do not present factual conclusions as researched. "
            "Write a short status report that states research is incomplete, names the objective, and explains "
            "that a Tavily search key is needed to retrieve external evidence.\n\n"
            f"Objective: {state['objective']}"
        )

    try:
        report = OpenAICompatibleModel().generate(prompt)
        model_status = "ready"
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        model_status = f"unavailable: {exc.__class__.__name__}"
        if sources:
            references = "\n".join(
                f"[{index}] {source['title']} - {source['url']}"
                for index, source in enumerate(sources, start=1)
            )
            report = (
                f"Research objective: {state['objective']}\n\n"
                f"Retrieved {len(sources)} source(s), but synthesis could not run because the configured model "
                "is unavailable. Review the source list below; no findings have been inferred.\n\n"
                f"Sources:\n{references}"
            )
        else:
            report = (
                f"Research objective: {state['objective']}\n\n"
                "Research is incomplete because no search provider is configured, so no external evidence was "
                "retrieved. Configure TAVILY_API_KEY to run source discovery."
            )
    return {"report": report, "model_status": model_status}


def build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("plan", plan_research)
    graph.add_node("search", collect_sources)
    graph.add_node("synthesize", synthesize_report)
    graph.set_entry_point("plan")
    graph.add_edge("plan", "search")
    graph.add_edge("search", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


research_graph = build_research_graph()
