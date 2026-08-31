"""The contract, checked against every game the loader can find.

Parametrising over `available_games()` rather than a hardcoded list is deliberate:
the held-out game in Phase 8 and anything the Phase 4 compiler produces get
conformance-tested the moment they exist, without anyone remembering to add them
here. That is the whole point of a fixed interface.
"""

from __future__ import annotations

import copy
import json
from types import ModuleType, SimpleNamespace

import pytest

from automa.core.game import (
    CORE_KEYS,
    LABEL,
    PLY,
    TO_PLAY,
    TURN,
    GameModule,
    Viewer,
    conforms,
    settle,
)
from automa.core.loader import available_games, load_game

GAMES = available_games()


@pytest.fixture(params=GAMES)
def game(request: pytest.FixtureRequest) -> ModuleType:
    return load_game(request.param)


def playable_state(game: ModuleType, seed: int = 1) -> dict:
    """A fresh state with every question it declares already answered.

    Since modules began asking the table for the components they are missing, a fresh
    state is routinely *waiting* rather than ready: `initial_state` returns a state
    carrying `unknown`, and `legal_actions` must not be called until that is cleared.
    `settle` is what an offline caller with nobody to ask uses - it answers every
    question `unavailable`, which is how the module falls back to its own seeded deal.
    Any test that steps a state, rather than only inspecting its shape, has to go
    through here first.
    """
    return settle(game, game.initial_state(seed))


def test_there_are_two_structurally_distant_games() -> None:
    """plan.md Phase 1 asks for two games so the interface cannot be shaped by one."""
    assert len(GAMES) >= 2, GAMES


def test_module_conforms(game: ModuleType) -> None:
    assert conforms(game) == []
    assert isinstance(game, GameModule)


def test_initial_state_is_deterministic_in_its_seed(game: ModuleType) -> None:
    """Phase 3's fixtures depend on this, as does replaying a validator failure."""
    assert game.initial_state(7) == game.initial_state(7)


def test_initial_state_is_well_formed(game: ModuleType) -> None:
    state = game.initial_state(1)
    for key in CORE_KEYS:
        assert key in state, f"{game.NAME} state is missing {key!r}"
    assert state[TO_PLAY] in game.PLAYERS
    assert isinstance(state[TURN], int) and isinstance(state[PLY], int)
    assert json.loads(json.dumps(state)) == state
    assert not game.is_terminal(state)


def test_legal_actions_are_non_empty_and_labelled(game: ModuleType) -> None:
    actions = game.legal_actions(playable_state(game))
    assert actions
    for action in actions:
        assert isinstance(action[LABEL], str) and action[LABEL]


def test_apply_returns_a_new_state_and_leaves_the_old_one_alone(game: ModuleType) -> None:
    state = playable_state(game)
    before = copy.deepcopy(state)
    new_state = game.apply(state, game.legal_actions(state)[0])
    assert state == before, f"{game.NAME}.apply mutated its input"
    assert new_state is not state
    assert new_state[PLY] > state[PLY]
    assert new_state[TURN] >= state[TURN]


def test_winner_is_undefined_before_the_end(game: ModuleType) -> None:
    """`None` must mean draw and only draw, so an unfinished match cannot return it."""
    with pytest.raises(ValueError):
        game.winner(game.initial_state(1))


def test_describe_state_renders_for_every_viewer(game: ModuleType) -> None:
    state = game.initial_state(1)
    for viewer in Viewer:
        text = game.describe_state(state, viewer)
        assert isinstance(text, str) and text.strip()


def test_check_invariants_passes_on_a_fresh_state(game: ModuleType) -> None:
    check = getattr(game, "check_invariants", None)
    if check is None:
        pytest.skip(f"{game.NAME} declares no invariants")
    assert check(game.initial_state(1)) == []


# -- guard the guard -------------------------------------------------------


def test_conforms_rejects_a_module_that_is_missing_the_interface() -> None:
    empty = SimpleNamespace()
    problems = conforms(empty)
    assert any("NAME" in p for p in problems)
    assert any("PLAYERS" in p for p in problems)
    assert any("legal_actions is missing" in p for p in problems)


def test_conforms_rejects_wrong_arity() -> None:
    """Phase 4 will meet this: a generated `apply` that forgot the action parameter."""
    broken = SimpleNamespace(
        NAME="broken",
        PLAYERS=("a", "b"),
        initial_state=lambda seed=None: {},
        legal_actions=lambda state: [],
        apply=lambda state: state,  # missing `action`
        is_terminal=lambda state: True,
        winner=lambda state: None,
        describe_state=lambda state, viewer: "",
    )
    assert any("apply must accept 2" in p for p in conforms(broken))


def test_conforms_rejects_players_that_are_not_two_distinct_names() -> None:
    same = SimpleNamespace(NAME="x", PLAYERS=("a", "a"))
    assert any("PLAYERS" in p for p in conforms(same))
