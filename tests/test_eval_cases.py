"""The case battery: does each case really test what it claims?

A battery whose cases are mislabelled is worse than no battery, because it produces
numbers that look like evidence. These tests check the claims, not the plumbing: that
every category is populated, that the Nim cases can actually distinguish good play from
bad, and that three positions hand-checked against the rules are what the module says
they are.
"""

from __future__ import annotations

import pytest

from automa.core.game import TO_PLAY
from automa.core.loader import load_game
from automa.eval.cases import BATTERY, CATEGORIES, Case, resolve, resolved_battery

RESOLVED = resolved_battery()


def test_the_battery_is_big_enough() -> None:
    """plan.md asks for 12 or more."""
    assert len(BATTERY) >= 12


def test_case_ids_are_unique() -> None:
    assert len({c.case_id for c in BATTERY}) == len(BATTERY)


def test_every_category_is_populated() -> None:
    used = {c.case.category for c in RESOLVED}
    assert used == set(CATEGORIES), f"unused: {set(CATEGORIES) - used}"


def test_every_category_used_is_documented() -> None:
    """A category with no explanation cannot be defended to a judge."""
    for case in BATTERY:
        assert case.category in CATEGORIES


def test_both_games_are_represented() -> None:
    games = {c.case.game for c in RESOLVED}
    assert games == {"nim", "dnup"}


@pytest.mark.parametrize("resolved", RESOLVED, ids=lambda r: r.case.case_id)
def test_every_case_is_a_live_decision_with_ground_truth(resolved) -> None:
    game = load_game(resolved.case.game)
    assert not game.is_terminal(resolved.state), "nothing to decide"
    assert resolved.legal, "no legal actions"
    assert resolved.to_play in game.PLAYERS
    assert resolved.state[TO_PLAY] == resolved.to_play


@pytest.mark.parametrize("resolved", RESOLVED, ids=lambda r: r.case.case_id)
def test_resolution_is_deterministic(resolved) -> None:
    """Both arms must be asked the identical question, on any machine, any run."""
    again = resolve(resolved.case)
    assert again.state == resolved.state
    assert again.legal == resolved.legal


def test_nim_cases_can_actually_distinguish_good_play_from_bad() -> None:
    """A lost position has no better move, so scoring quality there measures nothing.

    Two of the original picks were exactly that - every legal move equally optimal -
    and were replaced. This keeps them replaced.
    """
    nim_cases = [r for r in RESOLVED if r.case.game == "nim"]
    assert nim_cases
    for resolved in nim_cases:
        assert resolved.has_optimal
        assert len(resolved.optimal) < len(resolved.legal), (
            f"{resolved.case.case_id} is a lost position: all "
            f"{len(resolved.legal)} moves are equally optimal, so it cannot score quality"
        )


def test_dnup_cases_have_no_optimal_oracle() -> None:
    """DNUP is unsolved. Claiming a quality score there would be inventing one."""
    for resolved in (r for r in RESOLVED if r.case.game == "dnup"):
        assert not resolved.has_optimal


# -- hand-checked positions ------------------------------------------------
#
# Ground truth is derived from the module, which is the right source - it was
# validated in Phase 1 by 500 random matches and hand-computed unit tests. But
# "derived from the thing being tested" deserves at least a few positions checked
# against the rules by hand.


def test_hand_checked_nim_endgame() -> None:
    """heaps [2,0,0]: two objects in one heap. Taking both wins; taking one loses."""
    resolved = next(r for r in RESOLVED if r.case.case_id == "nim-endgame")
    assert resolved.state["heaps"] == [2, 0, 0]
    assert len(resolved.legal) == 2
    assert len(resolved.optimal) == 1
    assert resolved.optimal[0]["count"] == 2, "taking both is the only win"


def test_hand_checked_nim_open() -> None:
    """heaps [5,3,3]: nim-sum is 5, so the winning move empties heap A."""
    resolved = next(r for r in RESOLVED if r.case.case_id == "nim-open")
    assert resolved.state["heaps"] == [5, 3, 3]
    assert 5 ^ 3 ^ 3 == 5
    assert len(resolved.optimal) == 1
    assert resolved.optimal[0] == {"label": resolved.optimal[0]["label"], "heap": 0, "count": 5}


def test_hand_checked_dnup_opening_deal() -> None:
    """The opening deal: 13 cards each, nothing on the table, star holder to play."""
    resolved = next(r for r in RESOLVED if r.case.case_id == "dnup-open")
    from automa.games import dnup

    assert len(resolved.state["hands"]["automa"]) == 13
    assert len(resolved.state["hands"]["human"]) == 13
    assert all(not cards for p in dnup.PLAYERS for cards in resolved.state["areas"][p])
    assert resolved.state["area"] == 1, "the opener uses Play Area 2"
    assert any(c["id"] == dnup.STAR_CARD_ID for c in resolved.state["hands"][resolved.to_play])
    # Rotating your hand is always legal, so the list can never be empty.
    assert any(a["kind"] == "rotate" for a in resolved.legal)


def test_a_case_past_the_end_of_a_match_is_refused() -> None:
    """A silently-terminal case would score every arm as if it had answered."""
    with pytest.raises(ValueError):
        resolve(Case("bogus", "nim", 1, 99, "control"))
