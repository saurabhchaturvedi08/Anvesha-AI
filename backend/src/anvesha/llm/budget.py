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

    ``usable_fraction`` holds part of the budget back: research phases run with less than
    the full amount so that, when they use up their share, there is still enough left to
    write a (partial) report from what was found.
    """

    max_cost_usd: Decimal | None = None
    max_tokens: int | None = None
    spent_usd: Decimal = Decimal(0)
    used_tokens: int = 0
    calls: int = 0
    unpriced_calls: int = 0
    usable_fraction: Decimal = Decimal(1)

    def _cost_limit(self) -> Decimal | None:
        return None if self.max_cost_usd is None else self.max_cost_usd * self.usable_fraction

    def _token_limit(self) -> int | None:
        return None if self.max_tokens is None else int(self.max_tokens * self.usable_fraction)

    def check(self) -> None:
        cost_limit, token_limit = self._cost_limit(), self._token_limit()
        if cost_limit is not None and self.spent_usd >= cost_limit:
            raise BudgetExceededError(
                f"Cost budget reached: spent ${self.spent_usd:.4f} of ${cost_limit:.4f} "
                "available for this stage."
            )
        if token_limit is not None and self.used_tokens >= token_limit:
            raise BudgetExceededError(
                f"Token budget reached: used {self.used_tokens} of {token_limit} tokens "
                "available for this stage."
            )

    @property
    def exhausted(self) -> bool:
        try:
            self.check()
        except BudgetExceededError:
            return True
        return False

    def record(self, cost_usd: Decimal | None, usage: TokenUsage) -> None:
        self.calls += 1
        self.used_tokens += usage.total_tokens
        if cost_usd is None:
            self.unpriced_calls += 1
        else:
            self.spent_usd += cost_usd
