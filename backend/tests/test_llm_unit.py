from decimal import Decimal

import pytest

from anvesha.llm.budget import Budget
from anvesha.llm.catalog import PROVIDERS, deployment_access
from anvesha.llm.errors import BudgetExceededError
from anvesha.llm.pricing import estimate_cost_usd
from anvesha.llm.types import TokenUsage
from tests.conftest import make_settings

USAGE = TokenUsage(input_tokens=1000, output_tokens=500)


def test_known_model_has_a_price() -> None:
    cost = estimate_cost_usd(PROVIDERS["openai"], "gpt-4o-mini", USAGE)
    assert cost is not None and cost > 0


def test_unknown_model_price_is_none_and_local_models_are_free() -> None:
    assert estimate_cost_usd(PROVIDERS["openai"], "model-that-does-not-exist", USAGE) is None
    assert estimate_cost_usd(PROVIDERS["openai_compatible"], "anything", USAGE) is None
    assert estimate_cost_usd(PROVIDERS["ollama"], "llama3.2", USAGE) == 0


def test_budget_stops_after_cost_limit() -> None:
    budget = Budget(max_cost_usd=Decimal("0.01"))
    budget.check()
    budget.record(Decimal("0.006"), USAGE)
    budget.check()
    budget.record(Decimal("0.006"), USAGE)
    with pytest.raises(BudgetExceededError):
        budget.check()


def test_budget_counts_unpriced_calls_against_tokens_only() -> None:
    budget = Budget(max_cost_usd=Decimal("0.01"), max_tokens=2000)
    budget.record(None, USAGE)
    budget.check()
    assert (budget.spent_usd, budget.unpriced_calls, budget.used_tokens) == (0, 1, 1500)
    budget.record(None, USAGE)
    with pytest.raises(BudgetExceededError, match="Token budget"):
        budget.check()


def test_deployment_access_from_settings() -> None:
    settings = make_settings(openai_api_key="sk-deploy", ollama_base_url="http://ollama:11434/v1/")
    assert deployment_access(PROVIDERS["openai"], settings) == (
        "https://api.openai.com/v1",
        "sk-deploy",
    )
    assert deployment_access(PROVIDERS["ollama"], settings) == ("http://ollama:11434/v1", None)
    # Not configured: no key for Anthropic, and Ollama isn't assumed without a base URL.
    assert deployment_access(PROVIDERS["anthropic"], settings) is None
    assert deployment_access(PROVIDERS["ollama"], make_settings()) is None
