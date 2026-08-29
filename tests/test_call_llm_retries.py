"""The wrapper's retry loop, tested against the Provider protocol.

Deliberately no mocked SDK here: the tests drive a hand-written provider through
the same seam a real backend uses. If the seam were fake, these tests could not be
written this way - so they double as proof that a second provider can be dropped in
without touching `call_llm` or its callers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

import llm.call as call_module
from llm.call import LLMCallError, call_llm
from llm.providers.base import LLMRequest, LLMResponse
from trajectory import TrajectoryWriter

MODEL = "anthropic:claude-haiku-4-5"


class Decision(BaseModel):
    action_index: int
    justification: str


class ScriptedProvider:
    """Returns a canned response per attempt and records what it was sent."""

    name = "scripted"

    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self._responses[len(self.requests) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, TimeoutError)


def decision_response(index: int) -> LLMResponse:
    parsed = Decision(action_index=index, justification=f"picked {index}")
    return LLMResponse(
        text=parsed.model_dump_json(),
        parsed=parsed,
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=20,
        request_id=f"req_{index}",
        raw_content=[{"type": "text", "text": parsed.model_dump_json()}],
    )


@pytest.fixture
def install_provider(monkeypatch):
    def _install(provider):
        monkeypatch.setattr(call_module, "resolve", lambda _name: provider)
        return provider

    return _install


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
    calls = [e for e in read_events(path) if e["type"] == "llm_call"]
    assert calls[0]["outcome"] == "api_error"
    assert "connection reset" in calls[0]["error"]
    assert calls[1]["outcome"] == "ok"


def test_non_retryable_error_propagates(install_provider) -> None:
    install_provider(ScriptedProvider(PermissionError("bad key")))

    with pytest.raises(PermissionError):
        call_llm(
            agent="tactician",
            model=MODEL,
            messages=[{"role": "user", "content": "Choose."}],
        )
