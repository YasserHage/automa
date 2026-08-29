"""Cost accounting.

Cost per turn is a reported metric, and ground rule 9 requires every claim to trace
to evidence. The distinction these tests protect is between $0 because a model is
free and $0 because we do not know the price - those must never look the same.
"""

from __future__ import annotations

from automa.llm.pricing import PRICES, Price, cost_usd, pricing_note


def test_known_model_cost_matches_hand_calculation() -> None:
    # Opus 5 is $5.00/1M in, $25.00/1M out.
    # 1,000,000 in -> $5.00; 100,000 out -> $2.50. Total $7.50.
    cost = cost_usd(
        "anthropic:claude-opus-5", input_tokens=1_000_000, output_tokens=100_000
    )
    assert cost == 7.50


def test_haiku_is_cheaper_than_opus_for_identical_usage() -> None:
    usage = {"input_tokens": 10_000, "output_tokens": 500}
    assert cost_usd("anthropic:claude-haiku-4-5", **usage) < cost_usd(
        "anthropic:claude-opus-5", **usage
    )


def test_cache_reads_are_discounted_and_writes_are_surcharged() -> None:
    plain = cost_usd("anthropic:claude-opus-5", input_tokens=100_000, output_tokens=0)
    cached = cost_usd(
        "anthropic:claude-opus-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=100_000,
    )
    written = cost_usd(
        "anthropic:claude-opus-5",
        input_tokens=0,
        output_tokens=0,
        cache_write_tokens=100_000,
    )
    assert cached < plain < written


def test_unknown_model_returns_none_not_zero() -> None:
    cost = cost_usd("anthropic:not-a-real-model", input_tokens=1000, output_tokens=1000)
    # `is None`, not a falsy check: 0.0 would pass a falsy assertion and silently
    # under-report the cost-per-turn metric.
    assert cost is None
    assert pricing_note("anthropic:not-a-real-model") == "unpriced_model"


def test_free_tier_is_zero_but_labelled(monkeypatch) -> None:
    monkeypatch.setitem(PRICES, "google:gemini-flash-test", Price(0.0, 0.0, free_tier=True))
    cost = cost_usd("google:gemini-flash-test", input_tokens=50_000, output_tokens=5_000)
    assert cost == 0.0
    # The label is what separates this $0 from an unpriced model's None.
    assert pricing_note("google:gemini-flash-test") == "free_tier"


def test_priced_models_are_labelled_list_price() -> None:
    assert pricing_note("anthropic:claude-opus-5") == "list_price"
