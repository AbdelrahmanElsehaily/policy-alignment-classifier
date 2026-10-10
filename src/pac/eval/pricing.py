"""List prices used for the cost-per-event estimate (USD per million tokens)."""

from __future__ import annotations

from dataclasses import dataclass

from pac.schemas import Usage


@dataclass(frozen=True, slots=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


# Anthropic first-party list prices; cache writes are 1.25x input (5-minute TTL), reads 0.1x.
PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
}


def cost_usd(model: str, usage: Usage) -> float | None:
    """Cost of one call, or ``None`` when the model has no known price (local or free tiers).

    Follows Anthropic's usage semantics, where ``input_tokens`` excludes cached tokens.
    """
    price = PRICES.get(model)
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    ) / 1_000_000
