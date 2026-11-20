"""Cost estimates from Pydantic's genai-prices database (bundled; no network access)."""

from decimal import Decimal

import genai_prices

from anvesha.llm.catalog import ProviderSpec
from anvesha.llm.types import TokenUsage


def estimate_cost_usd(spec: ProviderSpec, model: str, usage: TokenUsage) -> Decimal | None:
    """USD cost of one call, 0 for free local providers, None when the price is unknown."""
    if spec.free:
        return Decimal(0)
    if spec.pricing_provider is None:
        return None
    try:
        price = genai_prices.calc_price(
            genai_prices.Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens),
            model,
            provider_id=spec.pricing_provider,
        )
    except LookupError:
        return None
    return Decimal(str(price.total_price))
