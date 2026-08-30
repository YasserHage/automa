"""The unknown-card loop, end to end. No model anywhere in this file.

This is the mechanism `plan.md` Phase 5 exists for: *"games with dozens of cards do not
need full description at setup"*. A card nobody has described is a counted deck member;
the first time one appears, the match pauses and asks; the answer is kept; the second
match of the same game asks fewer questions.

The last sentence is the exit criterion, and `test_the_second_match_asks_fewer_questions`
measures exactly it - offline, so the property is checked before any live run rather than
discovered to be broken during one.

A fixture game stands in for Sushi Go here. It is deliberately trivial: the loop being
tested is the core's, not any particular game's, and a real module would drag its own
rules into a test about information flow.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from automa.catalog import store
from automa.core.game import DEFAULT_PLAYERS, PLY, TO_PLAY, TURN, UNKNOWN
from automa.core.match import MissingInformant, play_match, random_decider
from automa.trajectory import TrajectoryWriter

#: What the fixture game reveals, in order. `tempura` twice on purpose: a card type is
#: described once, not once per copy.
REVEALS = ["tempura", "sashimi", "tempura", "chopsticks", "sashimi"]

EFFECTS = {
    "tempura": [{"op": "score_per_set", "size": 2, "points": 5}],
    "sashimi": [{"op": "score_per_set", "size": 3, "points": 10}],
    "chopsticks": None,  # the escape case - no scoring operation can express it
}


def build_game(catalog_data: dict | None = None) -> types.ModuleType:
    """A game that reveals one card per turn and stops when it meets an unfamiliar one."""
    seed_data = catalog_data or {"effects": {}, "described": []}

    def needed(state: dict) -> dict | None:
        if state["i"] >= len(REVEALS):
            return None
        card = REVEALS[state["i"]]
        # Already described covers both what is executable and what was parked.
        if card in state["catalog"] or card in state["parked"]:
            return None
        return {"kind": "card_effect", "card_type": card,
                "prompt": f"What does {card} do?"}

    def initial_state(seed: int | None = None) -> dict:
        base = {
            TO_PLAY: "automa", TURN: 0, PLY: 0, "i": 0,
            "catalog": dict(seed_data["effects"]),
            "parked": [c for c in seed_data["described"] if c not in seed_data["effects"]],
            "taken": [],
        }
        return {**base, UNKNOWN: needed(base)}

    def legal_actions(state: dict) -> list[dict]:
        if is_terminal(state):
            return []
        return [{"label": f"take {REVEALS[state['i']]}"}]

    def apply(state: dict, action: dict) -> dict:
        nxt = {
            **state,
            "i": state["i"] + 1,
            "taken": [*state["taken"], REVEALS[state["i"]]],
            TURN: state[TURN] + 1,
            PLY: state[PLY] + 1,
        }
        return {**nxt, UNKNOWN: needed(nxt)}

    def is_terminal(state: dict) -> bool:
        return state["i"] >= len(REVEALS)

    def winner(state: dict) -> str:
        if not is_terminal(state):
            raise ValueError("not terminal")
        return "automa"

    def resolve_unknown(state: dict, answer: dict) -> dict:
        card = answer["card_type"]
        if answer.get("effects"):
            nxt = {**state, "catalog": {**state["catalog"], card: answer["effects"]}}
        else:
            nxt = {**state, "parked": [*state["parked"], card]}
        return {**nxt, UNKNOWN: needed(nxt)}

    game = types.ModuleType("cardgame")
    game.NAME, game.PLAYERS = "cardgame", DEFAULT_PLAYERS
    game.initial_state = initial_state
    game.legal_actions = legal_actions
    game.apply = apply
    game.is_terminal = is_terminal
    game.winner = winner
    game.describe_state = lambda s, v: f"card {s['i']} of {len(REVEALS)}"
    game.resolve_unknown = resolve_unknown
    return game


def play(catalog: store.Catalog, seed_from_catalog: bool = False, trajectory=None):
    """One match. Returns (result, the card types it had to ask about)."""
    asked: list[str] = []

    def informant(ctx, unknown):
        card = unknown["card_type"]
        asked.append(card)
        effects = EFFECTS[card]
        if effects:
            catalog.learn(card, effects, "described at the table")
        else:
            catalog.park(card, "take two cards", "changes turn structure, not scoring")
        return {"card_type": card, "effects": effects}

    game = build_game(catalog.as_state_data() if seed_from_catalog else None)
    result = play_match(
        game,
        {p: random_decider(0) for p in game.PLAYERS},
        seed=1,
        informant=informant,
        trajectory=trajectory,
    )
    return result, asked


# -- the exit criterion ---------------------------------------------------


def test_the_second_match_asks_fewer_questions() -> None:
    """plan.md Phase 5, measured directly.

    Note the first match asks about three card *types* across five reveals - a type is
    described once, not once per copy. The second match asks about none of them.
    """
    catalog = store.Catalog(game="cardgame")

    first_result, first_asked = play(catalog)
    assert first_result.outcome == "terminal"
    assert sorted(first_asked) == ["chopsticks", "sashimi", "tempura"]
    assert first_result.informed == 3

    second_result, second_asked = play(catalog, seed_from_catalog=True)
    assert second_result.outcome == "terminal"
    assert second_asked == [], "everything was already known or parked"
    assert len(second_asked) < len(first_asked)


def test_a_card_type_is_described_once_not_once_per_copy() -> None:
    """Five cards are revealed but only three types exist."""
    catalog = store.Catalog(game="cardgame")
    _, asked = play(catalog)
    assert len(REVEALS) == 5
    assert len(asked) == 3
    assert len(set(asked)) == len(asked), "no type was asked about twice"


def test_a_parked_card_is_never_asked_about_again() -> None:
    """Chopsticks cannot be expressed, but the person still answered once."""
    catalog = store.Catalog(game="cardgame")
    play(catalog)
    assert catalog.parked() == {"chopsticks"}

    _, asked = play(catalog, seed_from_catalog=True)
    assert "chopsticks" not in asked


def test_the_catalog_survives_between_matches(tmp_path: Path) -> None:
    """The whole point: knowledge persists on disk, not just in memory."""
    catalog = store.Catalog(game="cardgame")
    play(catalog)
    path = catalog.save(tmp_path / "cardgame.json")

    reloaded = store.load("cardgame", path)
    assert reloaded.known() == {"tempura", "sashimi"}
    assert reloaded.parked() == {"chopsticks"}

    _, asked = play(reloaded, seed_from_catalog=True)
    assert asked == []


# -- information is not a decision ----------------------------------------


def test_supplying_a_fact_is_not_counted_as_an_intervention(tmp_path: Path) -> None:
    """Settled in Phase 2 and load-bearing here: being told what a card does is not the
    system getting a rule wrong. Conflating them would corrupt the primary metric."""
    catalog = store.Catalog(game="cardgame")
    result, asked = play(catalog)

    assert result.informed == len(asked) == 3
    assert sum(result.interventions.values()) == 0
    assert sum(result.fallbacks.values()) == 0
    assert result.first_intervention_turn is None


def test_information_is_logged_under_its_own_event(tmp_path: Path) -> None:
    catalog = store.Catalog(game="cardgame")
    with TrajectoryWriter("info", directory=tmp_path) as traj:
        play(catalog, trajectory=traj)
        path = traj.path

    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    told = [e for e in events if e.get("event") == "table_information"]
    assert len(told) == 3
    assert {e["kind"] for e in told} == {"card_effect"}
    assert "not a decision" in told[0]["note"]
    # And never as a decision the model made.
    assert not [e for e in events if e.get("event") == "baseline_intervention"]


def test_an_unknown_is_resolved_before_anyone_is_asked_to_decide() -> None:
    """A module missing a fact cannot meaningfully say what is legal."""
    seen: list[dict | None] = []

    def watcher(ctx):
        seen.append(ctx.state.get(UNKNOWN))
        return 0

    def informant(ctx, unknown):
        card = unknown["card_type"]
        effects = EFFECTS[card]
        return {"card_type": card, "effects": effects}

    game = build_game()
    play_match(game, {p: watcher for p in game.PLAYERS}, seed=1, informant=informant)
    assert all(u is None for u in seen), "no decider should ever see an open unknown"


# -- failure modes --------------------------------------------------------


def test_without_an_informant_the_match_refuses_to_proceed() -> None:
    """Inventing the answer would silently play a different game from the one on the table."""
    game = build_game()
    with pytest.raises(MissingInformant, match="no informant"):
        play_match(game, {p: random_decider(0) for p in game.PLAYERS}, seed=1)


def test_a_module_that_declares_an_unknown_but_cannot_resolve_it_is_caught() -> None:
    game = build_game()
    del game.resolve_unknown
    with pytest.raises(MissingInformant, match="resolve_unknown"):
        play_match(
            game,
            {p: random_decider(0) for p in game.PLAYERS},
            seed=1,
            informant=lambda ctx, u: {"card_type": "tempura", "effects": None},
        )
