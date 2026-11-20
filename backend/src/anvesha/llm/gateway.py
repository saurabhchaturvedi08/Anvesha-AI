"""The single entry point for model calls: route, budget, call, price, record usage."""

import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.models import ProviderCredential, UsageEvent
from anvesha.db.session import Database
from anvesha.llm.adapters import ChatAdapter
from anvesha.llm.budget import Budget
from anvesha.llm.catalog import PROVIDERS
from anvesha.llm.credentials import resolve_route
from anvesha.llm.errors import LLMAuthError, LLMError
from anvesha.llm.pricing import estimate_cost_usd
from anvesha.llm.types import AgentRole, Completion, Message, ModelRoute, TokenUsage, ToolSpec

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        vault: SecretBox | None,
        adapters: Mapping[str, ChatAdapter],
    ) -> None:
        self._db = db
        self._settings = settings
        self._vault = vault
        self._adapters = adapters  # keyed by ProviderSpec.api_style

    async def complete(
        self,
        *,
        workspace_id: uuid.UUID,
        role: AgentRole,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
        budget: Budget | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Completion:
        async with self._db.session() as session:
            route = await resolve_route(session, self._settings, self._vault, workspace_id, role)
        if budget is not None:
            budget.check()

        spec = PROVIDERS[route.access.provider]
        started = time.monotonic()
        try:
            completion = await self._adapters[spec.api_style].complete(
                route,
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                request_timeout=self._settings.llm_timeout_seconds,
            )
        except LLMError as exc:
            await self._record(
                workspace_id, project_id, role, route, TokenUsage(), None, started, exc
            )
            if isinstance(exc, LLMAuthError) and route.access.source == "workspace":
                await self._mark_credential_invalid(workspace_id, route.access.provider, str(exc))
            raise

        cost = estimate_cost_usd(spec, route.model, completion.usage)
        if budget is not None:
            budget.record(cost, completion.usage)
        await self._record(
            workspace_id, project_id, role, route, completion.usage, cost, started, None
        )
        return replace(completion, cost_usd=cost)

    async def _record(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
        role: AgentRole,
        route: ModelRoute,
        usage: TokenUsage,
        cost: Decimal | None,
        started: float,
        error: LLMError | None,
    ) -> None:
        event = UsageEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            role=role,
            provider=route.access.provider,
            model=route.model,
            credential_source=route.access.source,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
            status="error" if error else "ok",
            error_code=error.code if error else None,
        )
        try:
            async with self._db.session() as session:
                session.add(event)
                await session.commit()
        except SQLAlchemyError:
            # A failed usage write must not lose a completed (and paid-for) answer.
            logger.exception("Failed to record usage for %s/%s", event.provider, event.model)

    async def _mark_credential_invalid(
        self, workspace_id: uuid.UUID, provider: str, message: str
    ) -> None:
        try:
            async with self._db.session() as session:
                await session.execute(
                    update(ProviderCredential)
                    .where(
                        ProviderCredential.workspace_id == workspace_id,
                        ProviderCredential.provider == provider,
                    )
                    .values(status="invalid", last_error=message, last_checked_at=datetime.now(UTC))
                )
                await session.commit()
        except SQLAlchemyError:
            logger.exception("Failed to mark %s credential invalid", provider)
