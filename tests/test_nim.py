"""Nim: hand-computed positions, and the nim-sum oracle checked against brute force.

The oracle matters beyond Nim itself. Phase 3 and Phase 6 want to ask "did the
tactician play *well*?", not just "was the move legal", and Nim is the only game here
with an exact answer. An oracle that is subtly wrong would quietly corrupt those
measurements, so it is verified against exhaustive search rather than trusted.
"""

from __future__ import annotations

from functools import cache
from itertools import product

import pytest

from automa.core.game import PLY, TO_PLAY, TURN
from automa.games import nim


def state(heaps: list[int], to_play: str = "automa", last_mover: str | None = None) -> dict:
    return {TO_PLAY: to_play, TURN: 0, PLY: 0, "heaps": list(heaps), "last_mover": last_mover}


def test_one_object_left_is_a_win_for_whoever_takes_it() -> None:
    position = state([0, 0, 1])
    actions = nim.legal_actions(position)
    assert len(actions) == 1
    final = nim.apply(position, actions[0])
    assert nim.is_terminal(final)
    assert nim.winner(final) == "automa"
    assert final[TO_PLAY] == "human"


def test_legal_actions_enumerate_every_heap_and_count() -> None:
    # 2 + 3 objects means 2 + 3 = 5 distinct (heap, count) moves.
    assert len(nim.legal_actions(state([2, 3, 0]))) == 5
    assert nim.legal_actions(state([0, 0, 0])) == []


def test_apply_rejects_impossible_takes() -> None:
    with pytest.raises(ValueError):
        nim.apply(state([1, 1, 1]), {"heap": 0, "count": 2})
    with pytest.raises(ValueError):
        nim.apply(state([1, 1, 1]), {"heap": 9, "count": 1})


def test_winner_needs_a_terminal_position() -> None:
    with pytest.raises(ValueError):
        nim.winner(state([1, 0, 0]))


def test_check_invariants_flags_a_negative_heap() -> None:
    assert nim.check_invariants(state([-1, 0, 0]))
    assert nim.check_invariants(state([1, 2, 3])) == []


# -- the oracle, against exhaustive search ---------------------------------


@cache
def _player_to_move_wins(heaps: tuple[int, ...]) -> bool:
    """Exhaustive minimax over Nim positions - slow, obvious, and independent of XOR."""
    if not any(heaps):
        return False  # the previous player took the last object and has already won
    for index, size in enumerate(heaps):
        for count in range(1, size + 1):
            successor = list(heaps)
            successor[index] -= count
            if not _player_to_move_wins(tuple(successor)):
                return True
    return False


SMALL_POSITIONS = [h for h in product(range(4), repeat=3) if any(h)]


def test_nim_sum_agrees_with_brute_force_about_who_is_winning() -> None:
    for heaps in SMALL_POSITIONS:
        nim_sum = heaps[0] ^ heaps[1] ^ heaps[2]
        assert _player_to_move_wins(heaps) == (nim_sum != 0), heaps


def test_optimal_actions_actually_win_from_won_positions() -> None:
    for heaps in SMALL_POSITIONS:
        if not _player_to_move_wins(heaps):
            continue
        for action in nim.optimal_actions(state(list(heaps))):
            successor = list(heaps)
            successor[action["heap"]] -= action["count"]
            assert not _player_to_move_wins(tuple(successor)), (heaps, action)


def test_optimal_actions_from_a_lost_position_returns_everything() -> None:
    """A lost position has no winning move, so the oracle must not pretend otherwise."""
    lost = state([1, 2, 3])  # 1 ^ 2 ^ 3 == 0
    assert nim.optimal_actions(lost) == nim.legal_actions(lost)
