from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    id: str
    name: str
    type: str
    model: str
    status: str
    base_url: str | None = None
    note: str | None = None


class ResearchProjectCreate(BaseModel):
    title: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    research_depth: str = "standard"
    preferred_source_types: list[str] = Field(default_factory=list)


class ResearchPlanStep(BaseModel):
    id: str
    title: str
    description: str
    estimated_minutes: int


class ResearchPlan(BaseModel):
    project_id: str
    title: str
    objective: str
    status: str = "planned"
    tasks: list[ResearchPlanStep]


class ResearchExecutionResult(BaseModel):
    project_id: str
    status: str
    summary: dict[str, Any]
    report: str
    sources: int
    source_details: list[str] = Field(default_factory=list)
