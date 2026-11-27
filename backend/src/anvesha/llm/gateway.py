"""The single entry point for model calls: route, budget, call, price, record usage."""

import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from anvesha import tracing
from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.models import ProviderCredential, UsageEvent
from anvesha.db.session import Database
from anvesha.llm.adapters import ChatAdapter, EmbeddingAdapter
from anvesha.llm.budget import Budget
from anvesha.llm.catalog import PROVIDERS
from anvesha.llm.credentials import resolve_route
from anvesha.llm.errors import LLMAuthError, LLMError, ModelNotConfiguredError
from anvesha.llm.pricing import estimate_cost_usd
from anvesha.llm.types import (
    AgentRole,
    Completion,
    Embeddings,
    Message,
    ModelRole,
    ModelRoute,
    TokenUsage,
    ToolSpec,
)

# Most embedding APIs accept up to a few hundred inputs per request; stay well under.
EMBEDDING_BATCH_SIZE = 64

logger = logging.getLogger(__name__)


def _generation_attributes(
    route: ModelRoute, role: ModelRole, max_tokens: int | None, temperature: float | None
) -> dict[str, Any]:
    parameters = {
        key: value
        for key, value in (("max_tokens", max_tokens), ("temperature", temperature))
        if value is not None
    }
    return {
        "gen_ai.operation.name": "embeddings" if role == "embedding" else "chat",
        "gen_ai.provider.name": route.access.provider,
        "gen_ai.request.model": route.model,
        "gen_ai.request.max_tokens": max_tokens,
        "gen_ai.request.temperature": temperature,
        "langfuse.observation.model.name": route.model,
        "langfuse.observation.model.parameters": json.dumps(parameters) if parameters else None,
        "anvesha.role": role,
        # Whose key paid: the workspace's own, or the deployment's fallback key.
        "anvesha.credential_source": route.access.source,
    }


def _usage_attributes(usage: TokenUsage, cost: Decimal | None) -> dict[str, Any]:
    details = {"input": usage.input_tokens, "output": usage.output_tokens}
    return {
        "gen_ai.usage.input_tokens": usage.input_tokens,
        "gen_ai.usage.output_tokens": usage.output_tokens,
        "langfuse.observation.usage_details": json.dumps(details),
        # Our own price estimate, so Langfuse shows the same cost the budgets used.
        "langfuse.observation.cost_details": (
            json.dumps({"total": float(cost)}) if cost is not None else None
        ),
    }


class LLMGateway:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        vault: SecretBox | None,
        adapters: Mapping[str, ChatAdapter],
        embedder: EmbeddingAdapter | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._vault = vault
        self._adapters = adapters  # keyed by ProviderSpec.api_style
        self._embedder = embedder  # OpenAI-compatible providers; Anthropic has no embeddings

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
        run_id: uuid.UUID | None = None,
    ) -> Completion:
        async with self._db.session() as session:
            route = await resolve_route(session, self._settings, self._vault, workspace_id, role)
        if budget is not None:
            budget.check()

        spec = PROVIDERS[route.access.provider]
        with tracing.span(
            f"{role} · {route.model}",
            kind="generation",
            attributes=_generation_attributes(route, role, max_tokens, temperature)
            | {"anvesha.tools.offered": [tool.name for tool in tools] or None},
        ) as span:
            if tracing.content_enabled():
                tracing.set_input(span, tracing.chat_messages(messages))
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
                    workspace_id, project_id, role, route, TokenUsage(), None, started, exc, run_id
                )
                if isinstance(exc, LLMAuthError) and route.access.source == "workspace":
                    await self._mark_credential_invalid(
                        workspace_id, route.access.provider, str(exc)
                    )
                raise

            cost = estimate_cost_usd(spec, route.model, completion.usage)
            if budget is not None:
                budget.record(cost, completion.usage)
            await self._record(
                workspace_id, project_id, role, route, completion.usage, cost, started, None, run_id
            )
            tracing.set_attributes(
                span,
                _usage_attributes(completion.usage, cost)
                | {
                    "gen_ai.response.model": completion.model,
                    "gen_ai.response.finish_reasons": [completion.finish_reason]
                    if completion.finish_reason
                    else None,
                    "anvesha.tool_calls": [call.name for call in completion.tool_calls] or None,
                },
            )
            if tracing.content_enabled():
                tracing.set_output(
                    span,
                    {
                        "role": "assistant",
                        "content": completion.text,
                        "tool_calls": [
                            {"name": call.name, "arguments": call.arguments}
                            for call in completion.tool_calls
                        ],
                    },
                )
        return replace(completion, cost_usd=cost)

    async def embedding_route(self, workspace_id: uuid.UUID) -> ModelRoute | None:
        """The workspace's embedding model, or None when it hasn't chosen one."""
        try:
            return await self._embedding_route(workspace_id)
        except ModelNotConfiguredError:
            return None

    async def _embedding_route(self, workspace_id: uuid.UUID) -> ModelRoute:
        async with self._db.session() as session:
            route = await resolve_route(
                session, self._settings, self._vault, workspace_id, "embedding"
            )
        if PROVIDERS[route.access.provider].api_style != "openai" or self._embedder is None:
            raise ModelNotConfiguredError(
                f"{PROVIDERS[route.access.provider].name} has no embeddings API. "
                "Choose another provider for the embedding role."
            )
        return route

    async def embed(
        self,
        *,
        workspace_id: uuid.UUID,
        texts: Sequence[str],
        budget: Budget | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Embeddings:
        """Embed texts with the workspace's embedding model, in batches.

        Raises ModelNotConfiguredError when no embedding model is set up.
        """
        route = await self._embedding_route(workspace_id)
        assert self._embedder is not None  # checked by _embedding_route
        spec = PROVIDERS[route.access.provider]
        vectors: list[list[float]] = []
        input_tokens = 0
        total_cost: Decimal | None = Decimal(0)
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            if budget is not None:
                budget.check()
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            with tracing.span(
                f"embedding · {route.model}",
                kind="embedding",
                attributes=_generation_attributes(route, "embedding", None, None)
                | {"anvesha.inputs": len(batch)},
            ) as span:
                started = time.monotonic()
                try:
                    result = await self._embedder.embed(
                        route, batch, request_timeout=self._settings.llm_timeout_seconds
                    )
                except LLMError as exc:
                    await self._record(
                        workspace_id,
                        project_id,
                        "embedding",
                        route,
                        TokenUsage(),
                        None,
                        started,
                        exc,
                    )
                    if isinstance(exc, LLMAuthError) and route.access.source == "workspace":
                        await self._mark_credential_invalid(
                            workspace_id, route.access.provider, str(exc)
                        )
                    raise
                if vectors and len(result.vectors[0]) != len(vectors[0]):
                    raise LLMError(f"{spec.name}: embedding size changed between batches")
                cost = estimate_cost_usd(spec, route.model, result.usage)
                if budget is not None:
                    budget.record(cost, result.usage)
                await self._record(
                    workspace_id, project_id, "embedding", route, result.usage, cost, started, None
                )
                tracing.set_attributes(span, _usage_attributes(result.usage, cost))
            vectors.extend(result.vectors)
            input_tokens += result.usage.input_tokens
            total_cost = None if cost is None or total_cost is None else total_cost + cost
        return Embeddings(
            vectors=vectors,
            usage=TokenUsage(input_tokens=input_tokens),
            provider=route.access.provider,
            # The model id as configured (not as echoed back), so vectors stored now can
            # be matched with query vectors later.
            model=route.model,
            cost_usd=total_cost,
        )

    async def _record(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None,
        role: ModelRole,
        route: ModelRoute,
        usage: TokenUsage,
        cost: Decimal | None,
        started: float,
        error: LLMError | None,
        run_id: uuid.UUID | None = None,
    ) -> None:
        event = UsageEvent(
            workspace_id=workspace_id,
            project_id=project_id,
            run_id=run_id,
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
