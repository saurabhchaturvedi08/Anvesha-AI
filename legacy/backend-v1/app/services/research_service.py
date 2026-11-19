from __future__ import annotations

import json

import httpx

from app.schemas import ResearchPlan, ResearchPlanStep
from app.services.model_client import OpenAICompatibleModel


def generate_research_plan(project_id: str, title: str, objective: str, research_depth: str, preferred_sources: list[str]) -> ResearchPlan:
    depth_map = {
        "quick": 3,
        "standard": 5,
        "deep": 7,
    }
    task_count = depth_map.get(research_depth, 5)
    task_names = [
        "Define the research question",
        "Map relevant sources and repositories",
        "Collect evidence and notes",
        "Compare claims and contradictions",
        "Summarize findings with citations",
        "Validate assumptions and gaps",
        "Prepare final brief for action",
    ]
    tasks: list[ResearchPlanStep] = []
    for idx, task_name in enumerate(task_names[:task_count], start=1):
        tasks.append(
            ResearchPlanStep(
                id=f"step-{idx}",
                title=task_name,
                description=f"Execute step {idx} for: {objective}",
                estimated_minutes=10 + idx * 8,
            )
        )

    if preferred_sources:
        tasks.insert(
            1,
            ResearchPlanStep(
                id="step-source-priorities",
                title="Prioritize the selected source types",
                description=f"Focus on: {', '.join(preferred_sources)}.",
                estimated_minutes=15,
            ),
        )

    return ResearchPlan(
        project_id=project_id,
        title=title,
        objective=objective,
        status="planned",
        planning_mode="template",
        tasks=tasks,
    )


def _decode_json_object(response: str) -> dict[str, object]:
    content = response.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("The planning agent did not return a JSON object")
    return payload


def generate_agentic_research_plan(
    project_id: str,
    title: str,
    objective: str,
    research_depth: str,
    preferred_sources: list[str],
) -> tuple[ResearchPlan, str]:
    fallback = generate_research_plan(project_id, title, objective, research_depth, preferred_sources)
    task_limit = {"quick": 3, "standard": 5, "deep": 7}.get(research_depth, 5)
    prompt = (
        "You are the research planning agent in a bounded, evidence-first workflow. Break the objective into "
        f"at most {task_limit} independent research tasks. Each task must include a concise title, a concrete "
        "description, a search_query phrased for web search, and estimated_minutes as an integer. Prioritize "
        f"these source types when relevant: {', '.join(preferred_sources) or 'any credible sources'}. "
        "Return only a JSON object with a tasks array. Do not answer the research question.\n\n"
        f"Project: {title}\nObjective: {objective}"
    )
    try:
        payload = _decode_json_object(OpenAICompatibleModel().generate(prompt))
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            raise ValueError("The planning agent returned no task list")

        tasks: list[ResearchPlanStep] = []
        for item in raw_tasks[:task_limit]:
            if not isinstance(item, dict):
                continue
            task_title = item.get("title")
            search_query = item.get("search_query")
            if not isinstance(task_title, str) or not task_title.strip():
                continue
            if not isinstance(search_query, str) or not search_query.strip():
                search_query = f"{objective} {task_title.strip()}"
            description = item.get("description")
            estimated_minutes = item.get("estimated_minutes")
            tasks.append(
                ResearchPlanStep(
                    id=f"step-{len(tasks) + 1}",
                    title=task_title.strip()[:255],
                    description=description.strip()[:1000]
                    if isinstance(description, str) and description.strip()
                    else f"Investigate {task_title.strip()} in relation to: {objective}",
                    estimated_minutes=estimated_minutes
                    if isinstance(estimated_minutes, int) and not isinstance(estimated_minutes, bool)
                    else 15,
                    search_query=search_query.strip()[:500],
                )
            )
        if not tasks:
            raise ValueError("The planning agent returned no usable research tasks")

        return (
            ResearchPlan(
                project_id=project_id,
                title=title,
                objective=objective,
                status="planned",
                planning_mode="agentic",
                tasks=tasks,
            ),
            "ready",
        )
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        fallback.planning_mode = "template_fallback"
        return fallback, f"fallback:{exc.__class__.__name__}"


def serialize_plan(plan: ResearchPlan) -> str:
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)


def parse_plan(raw: str | None) -> ResearchPlan | None:
    if raw is None:
        return None
    payload = json.loads(raw)
    return ResearchPlan.model_validate(payload)
