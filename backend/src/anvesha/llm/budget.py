from dataclasses import dataclass
from decimal import Decimal

from anvesha.llm.errors import BudgetExceededError
from anvesha.llm.types import TokenUsage


@dataclass
class Budget:
    """Spending limits for one research run, checked before every model call.

    Limits are soft: calls already in flight when the limit is reached still complete,
    so parallel agents can overshoot slightly. Calls to models with unknown prices count
    toward the token limit but not the cost limit (see ``unpriced_calls``).
    """

    max_cost_usd: Decimal | None = None
    max_tokens: int | None = None
    spent_usd: Decimal = Decimal(0)
    used_tokens: int = 0
    calls: int = 0
    unpriced_calls: int = 0

    def check(self) -> None:
        if self.max_cost_usd is not None and self.spent_usd >= self.max_cost_usd:
            raise BudgetExceededError(
                f"Cost budget of ${self.max_cost_usd} reached (spent ${self.spent_usd})."
            )
        if self.max_tokens is not None and self.used_tokens >= self.max_tokens:
            raise BudgetExceededError(
                f"Token budget of {self.max_tokens} reached (used {self.used_tokens})."
            )

    def record(self, cost_usd: Decimal | None, usage: TokenUsage) -> None:
        self.calls += 1
        self.used_tokens += usage.total_tokens
        if cost_usd is None:
            self.unpriced_calls += 1
        else:
            self.spent_usd += cost_usd
