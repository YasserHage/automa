"""The baseline, driven by a scripted backend. Nothing here reaches a network.

The tests that matter most are the ones defending the *fairness* of the comparison,
because a baseline quietly crippled or quietly helped makes every number downstream
meaningless. Three properties are load-bearing:

* it is never shown the enumerated legal actions - that single difference is the
  hypothesis under test, and a leak would silently turn the baseline into the agent;
* it is never penalised for formatting;
* it is never told a move was illegal, because being told is exactly the corrective
  intervention the primary metric counts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _providers import ScriptedProvider, structured_response

from automa.contracts import BaselineMove
from automa.core.game import AUTOMA
from automa.core.loader import load_game
from automa.core.match import TurnContext, play_match, random_decider, viewer_for
from automa.eval.baseline import action_format, baseline_decider, match_action, rules_brief
from automa.trajectory import TrajectoryWriter

MODEL = "anthropic:claude-haiku-4-5"


@pytest.fixture(autouse=True)
def fixed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMA_MODEL_BASELINE", MODEL)


def move(action: dict, justification: str = "because") -> object:
    return structured_response(
        BaselineMove(action_json=json.dumps(action), justification=justification)
    )


def raw(text: str) -> object:
    return structured_response(BaselineMove(action_json=text, justification="because"))


def nim_context(trajectory=None):
    game = load_game("nim")
    state = game.initial_state(1)
    player = state["to_play"]
    return TurnContext(
        game=game,
        state=state,
        player=player,
        viewer=viewer_for(player),
        actions=game.legal_actions(state),
        turn=0,
        ply=0,
        trajectory=trajectory,
    )


def events(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


# -- fairness --------------------------------------------------------------


def test_the_baseline_is_never_shown_the_legal_actions(install_provider) -> None:
    """The load-bearing guarantee. If this fails, the comparison means nothing."""
    provider = install_provider(ScriptedProvider(move({"heap": 0, "count": 1})))
    ctx = nim_context()

    baseline_decider()(ctx)

    prompt = provider.requests[0].messages[0]["content"]
    for action in ctx.actions:
        assert action["label"] not in prompt, f"leaked a legal action: {action['label']}"
    assert "Legal actions:" not in prompt
    # ...but it *is* told the rules and the state, or it would be a strawman.
    assert "nim-sum" in prompt.lower() or "XOR" in prompt
    assert ctx.view() in prompt


def test_the_prompt_carries_the_rules_and_the_move_format(install_provider) -> None:
    provider = install_provider(ScriptedProvider(move({"heap": 0, "count": 1})))
    baseline_decider()(nim_context())

    prompt = provider.requests[0].messages[0]["content"]
    assert rules_brief("nim") in prompt
    assert action_format("nim") in prompt


def test_the_move_format_examples_come_from_the_opening_not_the_position() -> None:
    """Examples must never leak an answer for the position actually being asked."""
    text = action_format("dnup")
    assert "format" in text
    assert "not suggestions" in text


# -- ordinary play ---------------------------------------------------------


def test_a_legal_proposal_is_played(install_provider) -> None:
    install_provider(ScriptedProvider(move({"heap": 1, "count": 2}, "splits the heaps")))
    ctx = nim_context()

    decision = baseline_decider()(ctx)

    assert not decision.intervention and not decision.fallback
    assert ctx.actions[decision.index]["heap"] == 1
    assert ctx.actions[decision.index]["count"] == 2
    assert decision.justification == "splits the heaps"


def test_key_order_and_list_order_do_not_matter(install_provider) -> None:
    """Leniency runs in the baseline's favour, deliberately."""
    game = load_game("dnup")
    state = game.initial_state(1)
    actions = game.legal_actions(state)
    play = next(a for a in actions if a["kind"] == "play" and len(a["cards"]) > 1)

    reversed_cards = {"cards": list(reversed(play["cards"])), "kind": "play"}
    assert match_action(reversed_cards, actions) is not None


def test_a_label_in_the_proposal_is_ignored(install_provider) -> None:
    game = load_game("nim")
    actions = game.legal_actions(game.initial_state(1))
    assert match_action({**actions[0], "label": "nonsense"}, actions) == 0


# -- formatting is never the failure ---------------------------------------


def test_malformed_json_is_retried_with_feedback(install_provider) -> None:
    provider = install_provider(
        ScriptedProvider(raw("not json at all"), move({"heap": 0, "count": 1}))
    )

    decision = baseline_decider()(nim_context())

    assert not decision.intervention, "a formatting slip must not count as a rules error"
    followup = provider.requests[1].messages
    assert "not valid JSON" in followup[2]["content"]


def test_fenced_json_is_accepted(install_provider) -> None:
    """Markdown fences are a formatting habit, not a misunderstanding of the rules."""
    install_provider(ScriptedProvider(raw('```json\n{"heap": 0, "count": 1}\n```')))
    decision = baseline_decider()(nim_context())
    assert not decision.intervention


# -- illegal moves are interventions, never retried ------------------------


def test_an_illegal_move_is_recorded_and_substituted(install_provider, tmp_path: Path) -> None:
    install_provider(ScriptedProvider(move({"heap": 0, "count": 99})))

    with TrajectoryWriter("base", directory=tmp_path) as traj:
        ctx = nim_context(traj)
        decision = baseline_decider(seed=0)(ctx)
        path = traj.path

    assert decision.intervention, "an illegal move is a corrective intervention"
    assert not decision.fallback, "...and not an outage"
    assert 0 <= decision.index < len(ctx.actions), "the substitute is still legal"

    logged = [e for e in events(path) if e.get("event") == "baseline_intervention"]
    assert len(logged) == 1
    assert "illegal" in logged[0]["reason"]


def test_an_illegal_move_is_not_retried(install_provider) -> None:
    """Telling it the move was illegal IS the intervention; retrying would erase it."""
    provider = install_provider(ScriptedProvider(move({"heap": 0, "count": 99})))

    baseline_decider(seed=0)(nim_context())

    assert len(provider.requests) == 1, "the baseline must not get a second chance at legality"


def test_a_nonsense_shape_is_retried_and_only_then_gives_up(install_provider) -> None:
    """An unrecognisable shape is a formatting failure first, and only a rules
    failure once the baseline has been shown the format and still cannot produce it.

    Giving up straight away would score an encoding problem as a misunderstanding of
    the game; never giving up would let the format retry stand in for legality
    feedback, which is the thing being measured.
    """
    provider = install_provider(ScriptedProvider(*[move({"take": "everything"})] * 3))

    decision = baseline_decider(seed=0)(nim_context())

    assert len(provider.requests) == 3, "it should be shown the format and asked again"
    assert decision.fallback, "exhausted attempts is an unusable answer, not a rules error"
    assert not decision.intervention


# -- through the match loop ------------------------------------------------


def test_interventions_are_counted_across_a_match(install_provider, tmp_path: Path) -> None:
    """One match yields both the primary metric and the illegal-action count."""
    install_provider(ScriptedProvider(*[move({"heap": 0, "count": 99})] * 60))
    game = load_game("nim")

    with TrajectoryWriter("base", directory=tmp_path) as traj:
        result = play_match(
            game,
            {AUTOMA: baseline_decider(seed=0), "human": random_decider(0)},
            seed=1,
            trajectory=traj,
        )

    assert result.outcome == "terminal", "an illegal proposal must not end the match"
    assert result.interventions[AUTOMA] > 0
    assert result.first_intervention_turn is not None
    assert result.fallbacks[AUTOMA] == 0


def test_a_clean_match_reports_no_interventions(install_provider, tmp_path: Path) -> None:
    game = load_game("nim")
    state = game.initial_state(1)
    first = game.legal_actions(state)[0]
    install_provider(ScriptedProvider(*[move({"heap": first["heap"], "count": 1})] * 60))

    with TrajectoryWriter("base", directory=tmp_path) as traj:
        result = play_match(
            game,
            {AUTOMA: baseline_decider(seed=0), "human": random_decider(0)},
            seed=1,
            trajectory=traj,
        )

    assert result.first_intervention_turn is None or result.interventions[AUTOMA] == 0


def test_every_action_kind_the_game_offers_is_shown_in_the_format() -> None:
    """Regression: the opening DNUP position has an empty table.

    Deriving the format from ply 0 alone showed only `play` and `rotate`, so a
    baseline that wanted to take an opponent's set would have had to guess the key
    names - and would have failed on formatting rather than on the rules, which is
    the one failure mode this design exists to rule out.
    """
    text = action_format("dnup")
    for kind in ("play", "add", "take", "rotate"):
        assert f'"{kind}"' in text, f"the {kind} action shape is never shown"
    assert '"area"' in text and '"card"' in text


def test_a_wrong_value_type_is_a_format_error_not_a_rules_error(install_provider) -> None:
    """The real case that forced this distinction.

    Given a Nim board rendered `A: oo (2)  B: ooo (3)`, the live baseline answered
    `{"heap": "B", "count": 2}` - the right move, written with the heap named the way
    the *display* names it instead of as the integer index. Counting that as a rules
    failure would understate the baseline in exactly the way plan.md warns about.
    """
    provider = install_provider(
        ScriptedProvider(move({"heap": "B", "count": 2}), move({"heap": 1, "count": 2}))
    )

    decision = baseline_decider(seed=0)(nim_context())

    assert not decision.intervention, "an encoding slip is not a misunderstanding of the rules"
    assert len(provider.requests) == 2, "it should have been given the format back and retried"
    assert "not a well-formed action" in provider.requests[1].messages[2]["content"]


def test_format_feedback_never_leaks_what_is_legal_now(install_provider) -> None:
    """Shape feedback is derived from the game, not the position being asked about."""
    provider = install_provider(
        ScriptedProvider(move({"nonsense": 1}), move({"heap": 0, "count": 1}))
    )
    ctx = nim_context()

    baseline_decider(seed=0)(ctx)

    feedback = provider.requests[1].messages[2]["content"]
    for action in ctx.actions:
        assert action["label"] not in feedback


def test_a_well_formed_but_illegal_move_is_still_an_intervention(install_provider) -> None:
    """Right shape, impossible values: that is a rules failure and must not be retried."""
    provider = install_provider(ScriptedProvider(move({"heap": 0, "count": 99})))
    decision = baseline_decider(seed=0)(nim_context())
    assert decision.intervention
    assert len(provider.requests) == 1
