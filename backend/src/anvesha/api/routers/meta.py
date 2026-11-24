from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter

from anvesha import __version__
from anvesha.agents.clarifier import clarify
from anvesha.agents.context import PROFILES, WorkspaceCompleter
from anvesha.api.deps import GatewayDep, PrincipalDep, SettingsDep
from anvesha.api.schemas.meta import (
    ClarifyingQuestionRead,
    ClarifyRequest,
    ClarifyResponse,
    DepthInfo,
    ServerConfig,
)
from anvesha.llm.budget import Budget

router = APIRouter(prefix="/api/v1", tags=["meta"])

# Clarifying a question is one short model call; this only guards against runaway retries.
CLARIFY_MAX_COST_USD = Decimal("0.05")


@router.get("/config", response_model=ServerConfig)
async def server_config(settings: SettingsDep) -> ServerConfig:
    return ServerConfig(
        version=__version__,
        auth_mode=settings.auth_mode,
        run_default_max_cost_usd=settings.run_default_max_cost_usd,
        run_max_cost_ceiling_usd=settings.run_max_cost_ceiling_usd,
        chat_max_cost_usd=settings.chat_max_cost_usd,
        upload_max_bytes=settings.upload_max_bytes,
        depths={name: DepthInfo(**asdict(profile)) for name, profile in PROFILES.items()},
    )


@router.post("/clarify", response_model=ClarifyResponse)
async def clarify_question(
    payload: ClarifyRequest, gateway: GatewayDep, principal: PrincipalDep
) -> ClarifyResponse:
    """Ask the lead model whether a research question needs clarifying before a run.

    Optional: the web app offers it before starting research. Uses the ``lead`` role.
    """
    result = await clarify(
        WorkspaceCompleter(
            gateway=gateway,
            workspace_id=principal.workspace_id,
            budget=Budget(max_cost_usd=CLARIFY_MAX_COST_USD),
        ),
        payload.question,
    )
    return ClarifyResponse(
        ready=result.ready,
        questions=[ClarifyingQuestionRead(**q.model_dump()) for q in result.questions],
    )
