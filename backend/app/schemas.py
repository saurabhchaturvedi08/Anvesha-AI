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
    search_query: str | None = None


class ResearchPlan(BaseModel):
    project_id: str
    title: str
    objective: str
    status: str = "planned"
    planning_mode: str = "template"
    tasks: list[ResearchPlanStep]


class ResearchExecutionResult(BaseModel):
    project_id: str
    execution_id: str
    status: str
    summary: dict[str, Any]
    report: str
    sources: int
    source_details: list[dict[str, str]] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    research_findings: list[dict[str, Any]] = Field(default_factory=list)
    task_coverage: list[dict[str, Any]] = Field(default_factory=list)
    extracted_entities: list[dict[str, Any]] = Field(default_factory=list)
    extracted_dates: list[dict[str, Any]] = Field(default_factory=list)
    entity_extraction_status: str = "not_run"
    claims: list[dict[str, Any]] = Field(default_factory=list)
    analysis: list[dict[str, Any]] = Field(default_factory=list)
    verification_status: str = "not_run"
    agent_trace: list[dict[str, str]] = Field(default_factory=list)
    search_status: str
    search_agent_status: str = "not_run"
    supervisor_status: str = "not_run"
    model_status: str


class ResearchExecutionRequest(BaseModel):
    budget_limit: int | None = Field(default=None, ge=1)
    job_label: str | None = Field(default=None, max_length=255)


class DocumentIngestRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=2_000_000)


class DocumentSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)
