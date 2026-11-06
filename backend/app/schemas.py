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
    execution_id: str
    status: str
    summary: dict[str, Any]
    report: str
    sources: int
    source_details: list[dict[str, str]] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    search_status: str
    model_status: str


class DocumentIngestRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=2_000_000)


class DocumentSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)
