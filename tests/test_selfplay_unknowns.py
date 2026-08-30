"""Self-play against a module that is missing information.

A game whose card effects are learned at the table declares what it does not know and
stops. Before Phase 5 the harness knew nothing about that: it called `legal_actions` on
a state the module had already said it could not proceed from, and the first compiled
Sushi Go modules escaped by inventing a no-op "wait" action - which then could not
advance `ply`, so every match failed on a defect the harness had caused.

The harness now answers, the way `play_match` does, with the one answer available for
every question in every game: the fact could not be supplied. These tests pin that, and
pin the three ways a module can get the loop wrong.
"""

from __future__ import annotations

import types

from automa.core.game import PLY, TO_PLAY, TURN, UNAVAILABLE, UNKNOWN
from automa.core.selfplay import random_playout, validate_module

PLAYERS = ("automa", "human")


def _module(**overrides) -> types.ModuleType:
    """A minimal countdown game that asks about each token the first time it is played.

    Deliberately trivial as a *game* - a counter runs down and the match ends - so that
    anything the tests catch is about the unknown loop and not about the rules.
    """

    def initial_state(seed=None):
        return {
            TO_PLAY: PLAYERS[0],
            TURN: 0,
            PLY: 0,
            "left": 4,
            "deck": ["a", "b", "c", "d"],
            "described": [],
            "effects": {},
        }

    def legal_actions(state):
        if is_terminal(state):
            return []
        assert not state.get(UNKNOWN), "legal_actions was called with an unknown pending"
        return [{"label": f"play {state['deck'][0]}", "card": state["deck"][0]}]

    def apply(state, action):
        new = {**state, "deck": list(state["deck"][1:]), "left": state["left"] - 1}
        new[PLY] = state[PLY] + 1
        new[TURN] = state[TURN] + 1
        new[TO_PLAY] = PLAYERS[new[PLY] % 2]
        card = action["card"]
        if card not in state["described"]:
            new[UNKNOWN] = {
                "kind": "card_effect",
                "card_type": card,
                "prompt": f"What does {card} do?",
            }
        return new

    def resolve_unknown(state, answer):
        new = {**state, "described": [*state["described"], answer["card_type"]]}
        if not answer.get(UNAVAILABLE):
            new["effects"] = {**state["effects"], answer["card_type"]: answer["effects"]}
        new.pop(UNKNOWN, None)
        return new

    def is_terminal(state):
        return state["left"] <= 0

    def winner(state):
        if not is_terminal(state):
            raise ValueError("not terminal")
        return PLAYERS[0]

    def describe_state(state, viewer):
        return f"left={state['left']} described={state['described']}"

    ns = dict(
        NAME="askgame",
        PLAYERS=PLAYERS,
        initial_state=initial_state,
        legal_actions=legal_actions,
        apply=apply,
        is_terminal=is_terminal,
        winner=winner,
        describe_state=describe_state,
        resolve_unknown=resolve_unknown,
    )
    ns.update(overrides)
    module = types.ModuleType(ns["NAME"])
    module.__dict__.update(ns)
    return module


def test_a_module_that_declares_unknowns_completes_self_play():
    """The whole point: asking questions does not make a game unvalidatable."""
    report = validate_module(_module(), matches=5)
    assert report.ok, report.summary()
    assert report.outcomes == {"terminal": 5}


def test_every_question_is_asked_and_counted():
    result = random_playout(_module(), seed=0)
    assert result.ok
    assert result.unknowns == 4, "one question per distinct card"


def test_the_harness_answers_unavailable():
    """Self-play has nobody to ask, so no effect is ever learned - only recorded."""
    captured: list[dict] = []
    original = _module()

    def resolve_unknown(state, answer):
        captured.append(answer)
        return original.resolve_unknown(state, answer)

    random_playout(_module(resolve_unknown=resolve_unknown), seed=0)
    assert captured, "resolve_unknown was never called"
    assert all(a[UNAVAILABLE] is True for a in captured)
    assert all("effects" not in a for a in captured), "a value field must be absent, not None"
    assert {a["card_type"] for a in captured} == {"a", "b", "c", "d"}, (
        "the question's identifying fields must come back so the module knows what was answered"
    )


def test_legal_actions_is_never_called_with_an_unknown_pending():
    """Asserted inside the fixture; this names the property so a failure reads clearly."""
    assert random_playout(_module(), seed=3).ok


def test_declaring_an_unknown_without_resolve_unknown_is_a_violation():
    module = _module()
    del module.__dict__["resolve_unknown"]
    result = random_playout(module, seed=0)
    assert not result.ok
    checks = {v.check for v in result.violations}
    assert "declaring_an_unknown_requires_resolve_unknown" in checks


def test_a_resolve_that_never_clears_the_unknown_is_a_violation():
    """Otherwise the harness spins forever instead of reporting the defect."""

    def resolve_unknown(state, answer):
        return dict(state)  # leaves UNKNOWN in place

    result = random_playout(_module(resolve_unknown=resolve_unknown), seed=0)
    assert not result.ok
    assert "resolve_unknown_clears_what_it_answers" in {v.check for v in result.violations}


def test_a_malformed_unknown_is_a_violation():
    def apply(state, action):
        new = {**state, "left": state["left"] - 1, "deck": list(state["deck"][1:])}
        new[PLY] = state[PLY] + 1
        new[UNKNOWN] = "tell me about the card"  # a string, not a dict
        return new

    result = random_playout(_module(apply=apply), seed=0)
    assert not result.ok
    assert "unknown_is_well_formed" in {v.check for v in result.violations}


def test_answering_may_not_rewind_the_clock():
    def resolve_unknown(state, answer):
        new = {**state, "described": [*state["described"], answer["card_type"]]}
        new[PLY] = state[PLY] - 1
        new.pop(UNKNOWN, None)
        return new

    result = random_playout(_module(resolve_unknown=resolve_unknown), seed=0)
    assert "resolving_an_unknown_does_not_rewind_the_clock" in {
        v.check for v in result.violations
    }


def test_an_unknown_declared_by_initial_state_is_resolved_before_play():
    def initial_state(seed=None):
        state = _module().initial_state(seed)
        state[UNKNOWN] = {"kind": "card_effect", "card_type": "z", "prompt": "what is z?"}
        return state

    result = random_playout(_module(initial_state=initial_state), seed=0)
    assert result.ok, [str(v) for v in result.violations]
    assert result.unknowns == 5, "the deal's question plus one per card"


def test_a_game_with_no_unknowns_is_unaffected():
    """The nim/dnup path: no `unknown`, no `resolve_unknown`, nothing changes."""
    from automa.core.loader import load_game

    report = validate_module(load_game("nim"), matches=5)
    assert report.ok, report.summary()
    assert report.unknowns == 0
