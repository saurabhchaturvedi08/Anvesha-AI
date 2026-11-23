from decimal import Decimal

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from anvesha.config import Settings
from anvesha.db.models import ModelPreference, ProviderCredential, UsageEvent
from anvesha.llm.budget import Budget
from anvesha.llm.credentials import credential_aad
from anvesha.llm.errors import BudgetExceededError, LLMAuthError, ModelNotConfiguredError
from anvesha.llm.gateway import EMBEDDING_BATCH_SIZE, LLMGateway
from anvesha.llm.types import Message
from tests.conftest import FakeAdapter, make_settings


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    # A deployment-wide OpenAI key, to test the fallback path.
    return make_settings(database_url=database_url, openai_api_key="sk-deployment")


async def _prefer(app: FastAPI, role: str, provider: str, model: str) -> None:
    async with app.state.db.session() as session:
        session.add(
            ModelPreference(
                workspace_id=app.state.local_principal.workspace_id,
                role=role,
                provider=provider,
                model=model,
            )
        )
        await session.commit()


async def _add_workspace_key(app: FastAPI, provider: str, key: str) -> None:
    workspace_id = app.state.local_principal.workspace_id
    async with app.state.db.session() as session:
        session.add(
            ProviderCredential(
                workspace_id=workspace_id,
                provider=provider,
                encrypted_api_key=app.state.vault.encrypt(
                    key, credential_aad(workspace_id, provider)
                ),
                key_hint=key[-4:],
                status="valid",
            )
        )
        await session.commit()


async def _usage(app: FastAPI) -> list[UsageEvent]:
    async with app.state.db.session() as session:
        return list((await session.scalars(select(UsageEvent))).all())


def _gateway(app: FastAPI) -> LLMGateway:
    return app.state.gateway  # type: ignore[no-any-return]


async def test_role_without_preference_is_not_configured(
    app: FastAPI, fake_llm: FakeAdapter
) -> None:
    with pytest.raises(ModelNotConfiguredError, match="lead role"):
        await _gateway(app).complete(
            workspace_id=app.state.local_principal.workspace_id,
            role="lead",
            messages=[Message.user("hi")],
        )
    assert fake_llm.calls == []


async def test_deployment_key_is_the_fallback(app: FastAPI, fake_llm: FakeAdapter) -> None:
    await _prefer(app, "lead", "openai", "gpt-4o-mini")
    completion = await _gateway(app).complete(
        workspace_id=app.state.local_principal.workspace_id,
        role="lead",
        messages=[Message.user("hi")],
    )
    route = fake_llm.calls[0]["route"]
    assert (route.access.api_key, route.access.source) == ("sk-deployment", "deployment")
    assert completion.cost_usd is not None and completion.cost_usd > 0

    [event] = await _usage(app)
    assert (event.status, event.credential_source, event.role) == ("ok", "deployment", "lead")
    assert (event.input_tokens, event.output_tokens) == (1000, 500)
    assert event.cost_usd == completion.cost_usd


async def test_workspace_key_takes_priority(app: FastAPI, fake_llm: FakeAdapter) -> None:
    await _prefer(app, "researcher", "openai", "gpt-4o-mini")
    await _add_workspace_key(app, "openai", "sk-workspace-own")
    await _gateway(app).complete(
        workspace_id=app.state.local_principal.workspace_id,
        role="researcher",
        messages=[Message.user("hi")],
    )
    route = fake_llm.calls[0]["route"]
    assert (route.access.api_key, route.access.source) == ("sk-workspace-own", "workspace")


async def test_provider_without_credentials_is_not_configured(
    app: FastAPI, fake_llm: FakeAdapter
) -> None:
    await _prefer(app, "writer", "anthropic", "claude-x")
    with pytest.raises(ModelNotConfiguredError, match="anthropic"):
        await _gateway(app).complete(
            workspace_id=app.state.local_principal.workspace_id,
            role="writer",
            messages=[Message.user("hi")],
        )


async def test_budget_blocks_calls_once_spent(app: FastAPI, fake_llm: FakeAdapter) -> None:
    await _prefer(app, "lead", "openai", "gpt-4o-mini")
    budget = Budget(max_cost_usd=Decimal("0.0001"))
    workspace_id = app.state.local_principal.workspace_id
    await _gateway(app).complete(
        workspace_id=workspace_id, role="lead", messages=[Message.user("hi")], budget=budget
    )
    assert budget.calls == 1 and budget.spent_usd > 0
    with pytest.raises(BudgetExceededError):
        await _gateway(app).complete(
            workspace_id=workspace_id, role="lead", messages=[Message.user("hi")], budget=budget
        )
    assert len(fake_llm.calls) == 1


async def test_auth_failure_is_recorded_and_marks_workspace_key_invalid(
    app: FastAPI, fake_llm: FakeAdapter
) -> None:
    await _prefer(app, "lead", "openai", "gpt-4o-mini")
    await _add_workspace_key(app, "openai", "sk-revoked-key")
    fake_llm.error = LLMAuthError("openai: invalid key")

    with pytest.raises(LLMAuthError):
        await _gateway(app).complete(
            workspace_id=app.state.local_principal.workspace_id,
            role="lead",
            messages=[Message.user("hi")],
        )

    [event] = await _usage(app)
    assert (event.status, event.error_code, event.cost_usd) == (
        "error",
        "provider_auth_failed",
        None,
    )
    async with app.state.db.session() as session:
        credential = await session.scalar(select(ProviderCredential))
    assert credential is not None and credential.status == "invalid"


async def test_embeddings_are_batched_priced_and_recorded(
    app: FastAPI, fake_llm: FakeAdapter
) -> None:
    workspace_id = app.state.local_principal.workspace_id
    gateway = _gateway(app)
    assert await gateway.embedding_route(workspace_id) is None
    with pytest.raises(ModelNotConfiguredError, match="embedding role"):
        await gateway.embed(workspace_id=workspace_id, texts=["x"])

    await _prefer(app, "embedding", "openai", "text-embedding-3-small")
    texts = [f"passage {i}" for i in range(EMBEDDING_BATCH_SIZE + 6)]
    result = await gateway.embed(workspace_id=workspace_id, texts=texts)

    assert len(result.vectors) == len(texts)
    assert [len(batch) for batch in fake_llm.embed_calls] == [EMBEDDING_BATCH_SIZE, 6]
    assert result.model == "text-embedding-3-small"
    assert result.cost_usd is not None and result.cost_usd > 0
    events = await _usage(app)
    assert [(e.role, e.status) for e in events] == [("embedding", "ok")] * 2


async def test_anthropic_cannot_be_the_embedding_provider(
    app: FastAPI, fake_llm: FakeAdapter
) -> None:
    await _add_workspace_key(app, "anthropic", "sk-ant-workspace")
    await _prefer(app, "embedding", "anthropic", "claude-haiku-4-5")
    workspace_id = app.state.local_principal.workspace_id
    assert await _gateway(app).embedding_route(workspace_id) is None
    with pytest.raises(ModelNotConfiguredError, match="no embeddings API"):
        await _gateway(app).embed(workspace_id=workspace_id, texts=["x"])
    assert fake_llm.embed_calls == []
