"""Token pricing.

Cost per turn is a reported metric in the Improvement Changelog, and the hackathon
ground rules require every claim to trace back to evidence. So this module keeps
three outcomes strictly apart:

* **priced**    - a real dollar figure
* **free tier** - genuinely $0, flagged as such
* **unknown**   - ``None``

A bare ``0.0`` that could mean either "free" or "we don't know" is not evidence,
so an unrecognised model never returns a number.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """Dollars per 1M tokens."""

    input_per_mtok: float
    output_per_mtok: float
    free_tier: bool = False


# Anthropic list prices. Keyed by the full ``provider:model`` identifier so a
# same-named model on a different provider can never be priced by accident.
PRICES: dict[str, Price] = {
    "anthropic:claude-opus-5": Price(5.00, 25.00),
    "anthropic:claude-sonnet-5": Price(2.00, 10.00),
    "anthropic:claude-haiku-4-5": Price(1.00, 5.00),
    # A free-tier provider is added here with free_tier=True when one is wired up,
    # e.g. Price(0.0, 0.0, free_tier=True). See llm/providers/gemini_provider.py.
}

# Cache pricing is a multiple of the input rate.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Cost of one call, or ``None`` if the model has no registered price."""
    price = PRICES.get(model)
    if price is None:
        return None
    billable_input = (
        input_tokens
        + cache_read_tokens * CACHE_READ_MULTIPLIER
        + cache_write_tokens * CACHE_WRITE_MULTIPLIER
    )
    return (
        billable_input * price.input_per_mtok + output_tokens * price.output_per_mtok
    ) / 1_000_000


def pricing_note(model: str) -> str:
    """Provenance label attached to every logged cost, so a $0 is never ambiguous."""
    price = PRICES.get(model)
    if price is None:
        return "unpriced_model"
    return "free_tier" if price.free_tier else "list_price"
