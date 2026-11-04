from __future__ import annotations

import json
from typing import Any

from app.schemas import ResearchExecutionResult, ResearchPlan, ResearchPlanStep


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
        tasks=tasks,
    )


def execute_research_workflow(project_id: str, title: str, objective: str, preferred_sources: list[str]) -> ResearchExecutionResult:
    context = preferred_sources or ["local notes", "public docs", "memory"]
    source_details = [item for item in context]
    report_lines = [
        f"Research project: {title}",
        f"Objective: {objective}",
        f"Source focus: {', '.join(context)}",
        "",
        "Summary:",
        "- The workflow identified the primary question and mapped the likely evidence sources.",
        "- Local-first processing keeps the research workflow on-device and avoids unnecessary external calls.",
        "- The brief is ready for a second pass with deeper literature review and citation checks.",
    ]
    report = "\n".join(report_lines)
    summary = {
        "status": "completed",
        "tasks_completed": 5,
        "source_count": len(context),
        "confidence": "medium",
    }
    return ResearchExecutionResult(
        project_id=project_id,
        status="completed",
        summary=summary,
        report=report,
        sources=len(source_details),
        source_details=source_details,
    )


def serialize_plan(plan: ResearchPlan) -> str:
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)


def parse_plan(raw: str | None) -> ResearchPlan | None:
    if raw is None:
        return None
    payload = json.loads(raw)
    return ResearchPlan.model_validate(payload)
