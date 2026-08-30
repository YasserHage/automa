"""The Tactician, driven by a scripted backend. Nothing here reaches a network.

Three behaviours matter, and all three are about what happens when the model is
wrong or absent rather than when it is right:

* a hallucinated index is rejected, and the rejection is fed back so the retry is
  corrective rather than another roll of the dice;
* a model that never comes back does not end the match;
* whatever happens, the action that reaches the board came off the enumerated list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _providers import ScriptedProvider, decision_response

from automa.agents.tactician import tactician_decider
from automa.core.game import AUTOMA
from automa.core.loader import load_game
from automa.core.match import play_match, random_decider
from automa.trajectory import TrajectoryWriter

MODEL = "anthropic:claude-haiku-4-5"


@pytest.fixture(autouse=True)
def fixed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the tactician's model so the test does not depend on the config default."""
    monkeypatch.setenv("AUTOMA_MODEL_TACTICIAN", MODEL)


def nim_context(trajectory=None):
    """A real TurnContext from a real game, so the prompt is built from real material."""
    from automa.core.match import TurnContext, viewer_for

    game = load_game("nim")
    state = game.initial_state(1)
    player = state["to_play"]
    actions = game.legal_actions(state)
    return TurnContext(
        game=game,
        state=state,
        player=player,
        viewer=viewer_for(player),
        actions=actions,
        turn=0,
        ply=0,
        trajectory=trajectory,
    ), actions


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# -- the ordinary path -----------------------------------------------------


def test_a_valid_index_is_played(install_provider) -> None:
    install_provider(ScriptedProvider(decision_response(2)))
    ctx, _ = nim_context()

    decision = tactician_decider()(ctx)

    assert decision.index == 2
    assert not decision.fallback
    assert decision.justification is None, "the tactician returns an index and nothing else"


def test_the_prompt_carries_the_view_and_the_numbered_actions(install_provider) -> None:
    provider = install_provider(ScriptedProvider(decision_response(0)))
    ctx, actions = nim_context()

    tactician_decider()(ctx)

    sent = provider.requests[0].messages[0]["content"]
    assert ctx.view() in sent
    assert f"0. {actions[0]['label']}" in sent
    assert "Your strategy for this match" not in sent, "no policy exists until Phase 6"


def test_a_policy_is_included_when_one_exists(install_provider) -> None:
    """The Phase 6 slot, wired now so that phase changes no signatures."""
    provider = install_provider(ScriptedProvider(decision_response(0)))
    ctx, _ = nim_context()

    tactician_decider(policy="Deny the opponent even heaps.")(ctx)

    assert "Deny the opponent even heaps." in provider.requests[0].messages[0]["content"]


# -- when the model is wrong -----------------------------------------------


def test_an_illegal_index_is_rejected_and_retried_with_the_reason(
    install_provider, tmp_path: Path
) -> None:
    provider = install_provider(
        ScriptedProvider(decision_response(99), decision_response(1))
    )
    with TrajectoryWriter("tac", directory=tmp_path) as traj:
        ctx, _ = nim_context(traj)
        decision = tactician_decider()(ctx)
        path = traj.path

    assert decision.index == 1
    assert not decision.fallback

    # The retry was corrective: the model was shown what it said and why it failed.
    followup = provider.requests[1].messages
    assert followup[1]["role"] == "assistant"
    assert "outside the legal range" in followup[2]["content"]

    calls = [e for e in events(path) if e["type"] == "llm_call"]
    assert [e["outcome"] for e in calls] == ["validation_failed", "ok"]


def test_an_unreachable_model_falls_back_to_a_legal_action(
    install_provider, tmp_path: Path
) -> None:
    """A provider outage must not read as a rules failure, or end the match."""
    install_provider(ScriptedProvider(*[decision_response(99)] * 3))

    with TrajectoryWriter("tac", directory=tmp_path) as traj:
        ctx, actions = nim_context(traj)
        decision = tactician_decider()(ctx)
        path = traj.path

    assert decision.fallback
    assert 0 <= decision.index < len(actions), "a fallback is still a legal action"

    fallbacks = [
        e for e in events(path)
        if e["type"] == "core_event" and e.get("event") == "tactician_fallback"
    ]
    assert len(fallbacks) == 1, "the substitution must be on the record, not hidden"
    assert "outside the legal range" in fallbacks[0]["error"]


def test_a_transport_failure_is_retried_before_giving_up(install_provider) -> None:
    install_provider(ScriptedProvider(TimeoutError("connection reset"), decision_response(0)))
    ctx, _ = nim_context()

    decision = tactician_decider()(ctx)

    assert decision.index == 0 and not decision.fallback


# -- end to end, through the match loop ------------------------------------


def test_a_whole_match_runs_on_scripted_responses(install_provider, tmp_path: Path) -> None:
    """Automa always answers 0; the loop must still produce a complete, legal match."""
    install_provider(ScriptedProvider(*[decision_response(0)] * 200))
    game = load_game("nim")

    with TrajectoryWriter("nim", directory=tmp_path) as traj:
        result = play_match(
            game,
            {AUTOMA: tactician_decider(), "human": random_decider(0)},
            seed=1,
            trajectory=traj,
        )
        path = traj.path

    assert result.outcome == "terminal"
    assert result.fallbacks[AUTOMA] == 0

    # Every tactician decision is in the log - the Phase 2 exit criterion, checked
    # against the file rather than the screen. What makes a decision auditable is not a
    # sentence the model wrote about it but the record of what it was shown and what it
    # returned: the prompt, the numbered actions, and the index chosen.
    applied = [
        e for e in events(path)
        if e["type"] == "core_event" and e.get("event") == "action_applied"
        and e["player"] == AUTOMA
    ]
    assert applied
    assert all(e.get("index") is not None for e in applied)
    calls = [e for e in events(path) if e["type"] == "llm_call" and e["agent"] == "tactician"]
    assert calls and all(e["messages"] for e in calls)


def test_a_provider_outage_falls_back_instead_of_killing_the_match(
    install_provider, tmp_path: Path
) -> None:
    """Regression: a real run of 503s ended a DNUP match at ply 5.

    `call_llm` was re-raising the vendor's own exception once attempts ran out, so it
    slipped past the `except LLMCallError` the fallback is built on. The fallback had
    been tested only against validation failures, which never take that path.
    """
    install_provider(ScriptedProvider(*[TimeoutError("503 UNAVAILABLE")] * 3))

    with TrajectoryWriter("tac", directory=tmp_path) as traj:
        ctx, actions = nim_context(traj)
        decision = tactician_decider()(ctx)
        path = traj.path

    assert decision.fallback
    assert 0 <= decision.index < len(actions)
    fallbacks = [
        e for e in events(path)
        if e["type"] == "core_event" and e.get("event") == "tactician_fallback"
    ]
    assert len(fallbacks) == 1
    assert "503" in fallbacks[0]["error"]
