from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from anvesha.api.schemas.projects import Depth


class DepthInfo(BaseModel):
    max_rounds: int
    max_sub_questions: int
    max_follow_ups: int
    researcher_max_steps: int
    max_findings_per_researcher: int


class ServerConfig(BaseModel):
    """What clients need to know about this server's limits and modes."""

    version: str
    auth_mode: Literal["local", "jwt"]
    run_default_max_cost_usd: Decimal | None
    run_max_cost_ceiling_usd: Decimal | None
    chat_max_cost_usd: Decimal | None
    upload_max_bytes: int
    depths: dict[Depth, DepthInfo]


class ClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=10_000)


class ClarifyingQuestionRead(BaseModel):
    question: str
    why: str
    options: list[str]


class ClarifyResponse(BaseModel):
    ready: bool
    questions: list[ClarifyingQuestionRead]
