import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from anvesha.api.schemas.projects import Depth

RunStatus = Literal["queued", "running", "awaiting_approval", "completed", "failed", "cancelled"]


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str | None = Field(
        default=None,
        min_length=1,
        max_length=10_000,
        description="What to research. Defaults to the project's objective.",
    )
    depth: Depth | None = Field(default=None, description="Defaults to the project's depth.")
    autopilot: bool = Field(
        default=False, description="Skip plan approval and run straight through."
    )
    max_cost_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=10,
        decimal_places=4,
        description="Spending limit. Defaults to the server's per-run default.",
    )
    max_tokens: int | None = Field(default=None, ge=1000, le=100_000_000)


class PlanEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=8, max_length=600)
    rationale: str = Field(default="", max_length=1000)


class PlanApproval(BaseModel):
    """Approve the plan as proposed, or with edited sub-questions."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sub_questions: list[PlanEdit] | None = Field(default=None, min_length=1, max_length=12)
    strategy: str | None = Field(default=None, max_length=2000)


class SubQuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    round: int
    ordinal: int
    question: str
    rationale: str
    status: str
    coverage: str | None
    summary: str | None
    gaps: list[str]
    steps: int


class RunUsage(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    unpriced_calls: int


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    question: str
    depth: Depth
    autopilot: bool
    max_cost_usd: Decimal | None
    max_tokens: int | None
    plan: dict[str, Any] | None
    cancel_requested: bool
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunRead):
    sub_questions: list[SubQuestionRead]
    usage: RunUsage
    findings: dict[str, int] = Field(description="Number of findings by status.")
    report_id: uuid.UUID | None


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    agent: str | None
    data: dict[str, Any]
    created_at: datetime


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    sub_question_id: uuid.UUID | None
    source_id: uuid.UUID
    statement: str
    quote: str
    spans: list[list[int]]
    page: int | None
    status: str
    judge_note: str | None
    created_at: datetime


class ReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    version: int
    status: str
    title: str
    created_at: datetime


class ReportRead(ReportSummary):
    markdown: str
    citations: list[dict[str, Any]]
    details: dict[str, Any]
