"""The wrapper's retry loop, tested against the Provider protocol.

Deliberately no mocked SDK here: the tests drive a hand-written provider through
the same seam a real backend uses. If the seam were fake, these tests could not be
written this way - so they double as proof that a second provider can be dropped in
without touching `call_llm` or its callers.

`ScriptedProvider` and the `install_provider` fixture live in `_providers.py` and
`conftest.py`, so the Tactician tests drive the identical seam.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _providers import Decision, ScriptedProvider, decision_response

from automa.llm.call import LLMCallError, call_llm
from automa.trajectory import TrajectoryWriter

MODEL = "anthropic:claude-haiku-4-5"


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def only_legal(legal_count: int):
    def _validate(result):
        idx = result.parsed.action_index
        if not 0 <= idx < legal_count:
            raise ValueError(f"action_index {idx} is outside the legal range 0..{legal_count - 1}")

    return _validate


def test_invalid_then_valid_logs_both_attempts(install_provider, tmp_path: Path) -> None:
    provider = install_provider(ScriptedProvider(decision_response(99), decision_response(1)))

    with TrajectoryWriter("retry", directory=tmp_path) as traj:
        result = call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose one of 3 legal actions."}],
            output_format=Decision,
            validate=only_legal(3),
            trajectory=traj,
        )
        path = traj.path

    assert result.attempts == 2
    assert result.parsed.action_index == 1

    calls = [e for e in read_events(path) if e["type"] == "llm_call"]
    assert [e["attempt"] for e in calls] == [1, 2]
    assert [e["outcome"] for e in calls] == ["validation_failed", "ok"]
    assert "outside the legal range" in calls[0]["rejection"]
    assert calls[1]["rejection"] is None
    assert provider.requests[0].model == "claude-haiku-4-5"  # prefix stripped


def test_rejection_is_fed_back_into_the_next_attempt(install_provider) -> None:
    provider = install_provider(ScriptedProvider(decision_response(99), decision_response(0)))

    call_llm(
        agent="tactician",
        model=MODEL,
        messages=[{"role": "user", "content": "Choose one of 3 legal actions."}],
        output_format=Decision,
        validate=only_legal(3),
    )

    # The retry is corrective, not a re-roll: the second request carries what the
    # model said and why it was rejected.
    second = provider.requests[1].messages
    assert second[1]["role"] == "assistant"
    assert second[2]["role"] == "user"
    assert "outside the legal range" in second[2]["content"]


def test_exhausting_attempts_raises_with_the_last_rejection(install_provider) -> None:
    install_provider(ScriptedProvider(*[decision_response(99)] * 3))

    with pytest.raises(LLMCallError, match="outside the legal range"):
        call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
            output_format=Decision,
            validate=only_legal(3),
            max_attempts=3,
        )


def test_cost_and_latency_are_recorded_on_every_attempt(install_provider, tmp_path: Path) -> None:
    install_provider(ScriptedProvider(decision_response(0)))

    with TrajectoryWriter("cost", directory=tmp_path) as traj:
        result = call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
            output_format=Decision,
            trajectory=traj,
        )
        path = traj.path

    # Haiku 4.5: 120 in @ $1/1M + 20 out @ $5/1M
    assert result.cost_usd == pytest.approx((120 * 1.0 + 20 * 5.0) / 1_000_000)
    assert result.latency_ms >= 0

    call = next(e for e in read_events(path) if e["type"] == "llm_call")
    assert call["pricing_note"] == "list_price"
    assert call["cost_usd"] == pytest.approx(result.cost_usd)
    assert "latency_ms" in call
    # Invariant 3: the full prompt and the full response are both in the log.
    assert call["messages"][0]["content"] == "Choose."
    assert call["response_content"]


def test_retryable_transport_error_is_logged_then_retried(install_provider, tmp_path: Path) -> None:
    install_provider(ScriptedProvider(TimeoutError("connection reset"), decision_response(0)))

    with TrajectoryWriter("apierr", directory=tmp_path) as traj:
        result = call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
            output_format=Decision,
            trajectory=traj,
        )
        path = traj.path

    assert result.attempts == 2
    events = read_events(path)
    calls = [e for e in events if e["type"] == "llm_call"]
    assert calls[0]["outcome"] == "api_error"
    assert "connection reset" in calls[0]["error"]
    assert calls[1]["outcome"] == "ok"

    # A failed attempt is not a completed exchange, but the run must still show
    # that retrying was needed - otherwise a flaky provider looks free.
    end = events[-1]
    assert end["llm_calls"] == 1
    assert end["failed_attempts"] == 1


def test_non_retryable_error_propagates(install_provider) -> None:
    install_provider(ScriptedProvider(PermissionError("bad key")))

    with pytest.raises(PermissionError):
        call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
        )


def test_exhausting_attempts_on_a_transient_error_raises_llmcallerror(install_provider) -> None:
    """The documented contract, and the one the Tactician's fallback depends on.

    Raising the raw vendor exception here instead is what let a run of 503s kill a
    DNUP match whose Tactician had a working fallback ready: it caught LLMCallError,
    and a ServerError sailed straight past it.
    """
    install_provider(ScriptedProvider(*[TimeoutError("503 unavailable")] * 3))

    with pytest.raises(LLMCallError, match="503 unavailable"):
        call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
            max_attempts=3,
        )


def test_a_non_retryable_error_still_propagates_unchanged(install_provider) -> None:
    """A bad model name must not be softened into a retryable-looking failure.

    Degrading a misconfiguration to a fallback would hide it behind a whole match of
    random play, so this one stays loud.
    """
    install_provider(ScriptedProvider(PermissionError("404 model not found")))

    with pytest.raises(PermissionError):
        call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
        )
