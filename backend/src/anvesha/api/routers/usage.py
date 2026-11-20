from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from anvesha.api.deps import GatewayDep, PrincipalDep, SessionDep
from anvesha.api.schemas.llm import LLMTestRequest, LLMTestResponse, UsageByModel, UsageSummary
from anvesha.db.models import UsageEvent
from anvesha.llm.types import Message

router = APIRouter(prefix="/api/v1", tags=["usage"])


@router.get("/usage", response_model=UsageSummary)
async def usage_summary(
    session: SessionDep,
    principal: PrincipalDep,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> UsageSummary:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                UsageEvent.provider,
                UsageEvent.model,
                func.count(),
                func.count().filter(UsageEvent.status == "error"),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                func.count().filter(UsageEvent.cost_usd.is_(None), UsageEvent.status == "ok"),
            )
            .where(
                UsageEvent.workspace_id == principal.workspace_id, UsageEvent.created_at >= since
            )
            .group_by(UsageEvent.provider, UsageEvent.model)
            .order_by(func.coalesce(func.sum(UsageEvent.cost_usd), 0).desc())
        )
    ).all()
    by_model = [
        UsageByModel(
            provider=provider,
            model=model,
            calls=calls,
            errors=errors,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=Decimal(cost or 0),
            unpriced_calls=unpriced,
        )
        for provider, model, calls, errors, input_tokens, output_tokens, cost, unpriced in rows
    ]
    return UsageSummary(
        since=since,
        calls=sum(item.calls for item in by_model),
        input_tokens=sum(item.input_tokens for item in by_model),
        output_tokens=sum(item.output_tokens for item in by_model),
        cost_usd=sum((item.cost_usd for item in by_model), Decimal(0)),
        unpriced_calls=sum(item.unpriced_calls for item in by_model),
        by_model=by_model,
    )


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_model(
    payload: LLMTestRequest, gateway: GatewayDep, principal: PrincipalDep
) -> LLMTestResponse:
    """Send one prompt to the model configured for a role, to check your setup works."""
    completion = await gateway.complete(
        workspace_id=principal.workspace_id,
        role=payload.role,
        messages=[Message.user(payload.prompt)],
        max_tokens=payload.max_tokens,
    )
    return LLMTestResponse(
        provider=completion.provider,
        model=completion.model,
        text=completion.text,
        input_tokens=completion.usage.input_tokens,
        output_tokens=completion.usage.output_tokens,
        cost_usd=completion.cost_usd,
    )
