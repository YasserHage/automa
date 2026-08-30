"""The match loop, driven by scripted deciders. No model anywhere in this file.

The loop is where invariant 1 stops being prose. Two of these tests check it against
the trajectory rather than against the code: every action that was applied has a
`legal_actions_enumerated` event before it, at the same ply, listing the action that
was chosen. That is the property a judge can verify from a log file alone.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from types import ModuleType

import pytest

from automa.core.game import AUTOMA, HUMAN, TO_PLAY, Viewer, settle
from automa.core.loader import available_games, load_game
from automa.core.match import (
    Decision,
    IllegalDecision,
    MatchAbandoned,
    TurnContext,
    play_match,
    random_decider,
    uninformed,
    viewer_for,
)
from automa.trajectory import TrajectoryWriter

GAMES = available_games()


@pytest.fixture(params=GAMES)
def game(request: pytest.FixtureRequest) -> ModuleType:
    return load_game(request.param)


def randoms(game: ModuleType, seed: int = 0) -> dict:
    return {p: random_decider(seed + i) for i, p in enumerate(game.PLAYERS)}


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def core_events(path: Path, name: str) -> list[dict]:
    return [e for e in events(path) if e["type"] == "core_event" and e.get("event") == name]


# -- playing a match -------------------------------------------------------


def test_a_match_runs_to_a_terminal_state(game: ModuleType) -> None:
    result = play_match(game, randoms(game), seed=1, informant=uninformed)
    assert result.outcome == "terminal"
    assert result.winner in (*game.PLAYERS, None)
    assert result.plies > 0
    assert not result.invariant_violations


def test_the_same_seed_replays_the_same_match(game: ModuleType) -> None:
    """Needed by Phase 3: a battery is only comparable if a case is reproducible."""
    first = play_match(game, randoms(game), seed=7, informant=uninformed)
    second = play_match(game, randoms(game), seed=7, informant=uninformed)
    assert (first.winner, first.plies, first.turns) == (second.winner, second.plies, second.turns)


def test_max_plies_aborts_rather_than_hanging(game: ModuleType) -> None:
    result = play_match(game, randoms(game), seed=1, max_plies=2, informant=uninformed)
    assert result.outcome == "aborted"
    assert result.plies == 2


def test_a_decider_may_end_the_match(game: ModuleType) -> None:
    """How the CLI's `q` gets out without crashing the trajectory writer."""

    def quitter(ctx: TurnContext) -> int:
        raise MatchAbandoned

    deciders = {**randoms(game), settle(game, game.initial_state(1))[TO_PLAY]: quitter}
    result = play_match(game, deciders, seed=1, informant=uninformed)
    assert result.outcome == "abandoned"


# -- invariant 1, checked against the log ----------------------------------


def test_every_applied_action_was_enumerated_first(game: ModuleType, tmp_path: Path) -> None:
    with TrajectoryWriter("match", directory=tmp_path) as traj:
        play_match(game, randoms(game), seed=3, trajectory=traj, informant=uninformed)
        path = traj.path

    enumerated = {e["ply"]: e for e in core_events(path, "legal_actions_enumerated")}
    applied = core_events(path, "action_applied")
    assert applied, "a match that applied no action proves nothing"

    for event in applied:
        offer = enumerated.get(event["ply"])
        assert offer is not None, f"ply {event['ply']} applied an action nobody enumerated"
        assert offer["to_play"] == event["player"]
        # The chosen action was on the list, at the index that was reported.
        assert offer["actions"][event["index"]] == event["action"]


def test_the_match_is_bookended_in_the_trajectory(game: ModuleType, tmp_path: Path) -> None:
    with TrajectoryWriter("match", directory=tmp_path) as traj:
        result = play_match(game, randoms(game), seed=3, trajectory=traj, informant=uninformed)
        path = traj.path

    start = core_events(path, "match_start")
    end = core_events(path, "match_end")
    assert len(start) == len(end) == 1
    assert start[0]["game"] == game.NAME
    assert end[0]["winner"] == result.winner
    assert end[0]["plies"] == result.plies


# -- rejecting deciders that misbehave -------------------------------------


def test_an_out_of_range_index_is_refused(game: ModuleType) -> None:
    """Clamping would hide the bug and corrupt the match at the same time."""

    def cheat(ctx: TurnContext) -> int:
        return len(ctx.actions)  # one past the end

    with pytest.raises(IllegalDecision, match="only 0.."):
        play_match(game, {p: cheat for p in game.PLAYERS}, seed=1, informant=uninformed)


def test_a_negative_index_is_refused(game: ModuleType) -> None:
    """Python would happily read -1 as the last action."""
    with pytest.raises(IllegalDecision):
        play_match(game, {p: (lambda ctx: -1) for p in game.PLAYERS}, seed=1,
                   informant=uninformed)


def test_a_decider_returning_the_wrong_type_is_refused() -> None:
    game = load_game("nim")
    with pytest.raises(IllegalDecision, match="action index"):
        play_match(game, {p: (lambda ctx: "take one") for p in game.PLAYERS}, seed=1)


def test_a_missing_decider_is_caught_before_the_match_starts() -> None:
    game = load_game("nim")
    with pytest.raises(ValueError, match="no decider"):
        play_match(game, {game.PLAYERS[0]: random_decider(0)}, seed=1)


# -- fallbacks and the reserved seams --------------------------------------


def test_fallback_decisions_are_counted_separately(game: ModuleType) -> None:
    """A fallback is still a legal move, but it must not pass as a decision."""

    def always_fallback(ctx: TurnContext) -> Decision:
        return Decision(index=0, justification="model unreachable", fallback=True)

    deciders = {**randoms(game), AUTOMA: always_fallback}
    result = play_match(game, deciders, seed=1, informant=uninformed)
    assert result.fallbacks[AUTOMA] > 0
    assert result.fallbacks[HUMAN] == 0


def test_a_supplied_initial_state_replaces_the_seeded_deal() -> None:
    """The Phase 7 seam: start from what was actually dealt at the table."""
    game = load_game("nim")
    start = game.initial_state(5)
    result = play_match(game, randoms(game), seed=99, initial=start)
    assert result.outcome == "terminal"


def test_an_informant_is_harmless_for_a_game_that_needs_nothing() -> None:
    """Nim never lacks a fact, so its informant is never called."""
    game = load_game("nim")
    calls: list[object] = []

    result = play_match(
        game, randoms(game), seed=1,
        informant=lambda ctx, unknown: calls.append(unknown) or {},
    )
    assert result.outcome == "terminal"
    assert result.informed == 0
    assert not calls, "a game with no unknowns must never ask"


def test_a_game_needing_information_without_an_informant_fails_loudly() -> None:
    """Guessing the answer would silently play a different game from the one on the table."""
    from automa.core.game import UNKNOWN
    from automa.core.match import MissingInformant

    game = load_game("nim")
    needy = types.ModuleType("needy")
    for attr in dir(game):
        if not attr.startswith("__"):
            setattr(needy, attr, getattr(game, attr))
    needy.NAME = "needy"
    needy.initial_state = lambda seed=None: {
        **game.initial_state(seed),
        UNKNOWN: {"kind": "card_effect", "prompt": "what does this do?"},
    }

    with pytest.raises(MissingInformant, match="no informant"):
        play_match(needy, randoms(game), seed=1)


def test_a_module_that_never_clears_its_unknown_is_bounded() -> None:
    """A resolve_unknown that does not resolve would otherwise hang the match."""
    from automa.core.game import UNKNOWN
    from automa.core.match import MissingInformant

    game = load_game("nim")
    stuck = types.ModuleType("stuck")
    for attr in dir(game):
        if not attr.startswith("__"):
            setattr(stuck, attr, getattr(game, attr))
    stuck.NAME = "stuck"
    stuck.initial_state = lambda seed=None: {
        **game.initial_state(seed),
        UNKNOWN: {"kind": "loop", "prompt": "again?"},
    }
    stuck.resolve_unknown = lambda state, answer: state  # never clears it

    with pytest.raises(MissingInformant, match="still reports missing information"):
        play_match(stuck, randoms(game), seed=1, informant=lambda ctx, u: {})


# -- the view boundary -----------------------------------------------------


def test_each_player_decides_through_their_own_view(game: ModuleType) -> None:
    """The core picks the lens, not the decider that would benefit from a wider one."""
    seen: list[tuple[str, Viewer]] = []

    def watcher(ctx: TurnContext) -> int:
        seen.append((ctx.player, ctx.viewer))
        return 0

    play_match(
        game, {p: watcher for p in game.PLAYERS}, seed=1, max_plies=4, informant=uninformed
    )
    assert seen
    for player, viewer in seen:
        assert viewer == viewer_for(player)
        assert viewer.value == player


def test_an_unusual_player_name_falls_back_to_the_public_view() -> None:
    """Losing information is the safe direction; leaking it is not."""
    assert viewer_for("dealer") == Viewer.PUBLIC
