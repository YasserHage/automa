"""Guard the guard: the harness is fed broken modules and must catch each one.

A validator that has never failed proves nothing. Phase 4 leans on this harness to
decide whether a *generated* module is safe to put in front of a person, so the
question "would it actually notice?" has to be answered here, deliberately, rather
than assumed because the two hand-written games happen to pass.

Each fake below breaks exactly one property, so a missed detection names the check
that stopped working.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from automa.core.game import LABEL, PENDING, PLY, TO_PLAY, TURN
from automa.core.selfplay import random_playout, validate_module


def countdown(**overrides: Any) -> SimpleNamespace:
    """A minimal sound game: tick a counter down to zero, alternating players."""
    module = SimpleNamespace(
        NAME="countdown",
        PLAYERS=("a", "b"),
        initial_state=lambda seed=None: {TO_PLAY: "a", TURN: 0, PLY: 0, "n": 3},
        legal_actions=lambda s: [] if s["n"] == 0 else [{LABEL: "tick"}],
        apply=lambda s, a: {
            **s,
            "n": s["n"] - 1,
            TURN: s[TURN] + 1,
            PLY: s[PLY] + 1,
            TO_PLAY: "b" if s[TO_PLAY] == "a" else "a",
        },
        is_terminal=lambda s: s["n"] == 0,
        winner=lambda s: "a",
        describe_state=lambda s, v: "counting",
    )
    for key, value in overrides.items():
        setattr(module, key, value)
    return module


def checks(module: SimpleNamespace, max_plies: int = 20) -> set[str]:
    return {v.check for v in random_playout(module, seed=0, max_plies=max_plies).violations}


def test_the_control_module_passes() -> None:
    """If the sound module failed, every result below would be meaningless."""
    result = random_playout(countdown(), seed=0)
    assert result.ok and result.outcome == "terminal" and result.winner == "a"


def test_a_mutating_apply_is_caught() -> None:
    def mutates(state: dict, action: dict) -> dict:
        state["n"] -= 1  # the bug: the caller's state is modified
        return {**state, TURN: state[TURN] + 1, PLY: state[PLY] + 1}

    assert "apply_does_not_mutate_its_input" in checks(countdown(apply=mutates))


def test_a_dead_state_is_caught() -> None:
    module = countdown(legal_actions=lambda s: [], is_terminal=lambda s: False)
    assert "legal_actions_non_empty_off_terminal" in checks(module)


def test_a_match_that_never_ends_is_reported_as_aborted() -> None:
    module = countdown(is_terminal=lambda s: False, legal_actions=lambda s: [{LABEL: "tick"}])
    result = random_playout(module, seed=0, max_plies=15)
    assert result.outcome == "aborted" and result.plies == 15
    assert not result.ok


def test_a_ply_counter_that_stands_still_is_caught() -> None:
    """Without this, a livelock inside a single turn would look like progress."""
    module = countdown(
        apply=lambda s, a: {**s, "n": s["n"] - 1, TURN: s[TURN] + 1},
        is_terminal=lambda s: False,
    )
    assert "ply_strictly_increases" in checks(module)


def test_a_turn_counter_running_backwards_is_caught() -> None:
    module = countdown(
        apply=lambda s, a: {**s, "n": s["n"] - 1, TURN: s[TURN] - 1, PLY: s[PLY] + 1},
    )
    assert "turn_is_non_decreasing" in checks(module)


def test_a_state_that_does_not_survive_json_is_caught() -> None:
    """Tuples are the usual culprit: they serialise fine and come back as lists."""
    module = countdown(
        apply=lambda s, a: {**s, "n": s["n"] - 1, TURN: s[TURN] + 1, PLY: s[PLY] + 1, "t": (1, 2)},
    )
    assert "state_round_trips_through_json" in checks(module)


def test_an_unknown_player_to_play_is_caught() -> None:
    module = countdown(
        apply=lambda s, a: {**s, "n": s["n"] - 1, TURN: s[TURN] + 1, PLY: s[PLY] + 1, TO_PLAY: "z"},
    )
    assert "to_play_is_a_known_player" in checks(module)


def test_a_module_declaring_a_broken_invariant_is_caught() -> None:
    module = countdown(check_invariants=lambda s: ["the widgets do not add up"])
    assert "check_invariants" in checks(module)


def test_an_exception_during_play_is_a_finding_not_a_crash() -> None:
    def explodes(state: dict, action: dict) -> dict:
        raise RuntimeError("boom")

    result = random_playout(countdown(apply=explodes), seed=0)
    assert result.outcome == "error"
    assert any("boom" in v.detail for v in result.violations)


def test_a_winner_who_is_not_a_player_is_caught() -> None:
    assert "winner_is_a_known_player_or_none" in checks(countdown(winner=lambda s: "nobody"))


# -- the pending-decision convention ---------------------------------------
#
# Neither Phase 1 game needs a sub-decision, so these two fakes are the only thing
# exercising the PENDING contract. Left untested it would be a convention that has
# never run, waiting to be discovered as broken by the first game that needs it.


def test_a_pending_decision_belonging_to_the_wrong_player_is_caught() -> None:
    module = countdown(
        apply=lambda s, a: {
            **s,
            "n": s["n"] - 1,
            TURN: s[TURN] + 1,
            PLY: s[PLY] + 1,
            PENDING: {"player": "b", "kind": "discard"},
            TO_PLAY: "a",
        },
    )
    assert "pending_belongs_to_the_player_to_play" in checks(module)


def test_resolving_a_pending_decision_may_not_advance_the_turn() -> None:
    module = countdown(
        initial_state=lambda seed=None: {
            TO_PLAY: "a",
            TURN: 0,
            PLY: 0,
            "n": 3,
            PENDING: {"player": "a", "kind": "keep"},
        },
    )
    assert "resolving_a_pending_decision_does_not_advance_the_turn" in checks(module)


# -- aggregation -----------------------------------------------------------


def test_validate_module_reports_outcomes_and_keeps_the_seed_of_each_failure() -> None:
    report = validate_module(countdown(), matches=5)
    assert report.ok
    assert report.outcomes == {"terminal": 5}
    assert report.winners == {"a": 5}

    broken = validate_module(countdown(winner=lambda s: "nobody"), matches=3)
    assert not broken.ok
    assert {v.seed for v in broken.violations} == {0, 1, 2}


def test_an_aborted_match_fails_validation_even_with_no_violations() -> None:
    """An abort is not a crash, but it is not a completed match either."""
    endless = countdown(is_terminal=lambda s: False, legal_actions=lambda s: [{LABEL: "tick"}])
    report = validate_module(endless, matches=2, max_plies=5)
    assert not report.violations
    assert not report.ok
    assert report.outcomes == {"aborted": 2}


def test_a_module_that_cannot_render_its_state_is_caught() -> None:
    """Regression from Phase 4, and the reason this check exists.

    The harness exercised five of the six contract methods and skipped
    `describe_state`. A compiled DNUP module that raised `AttributeError` the moment
    anyone looked at the board therefore passed 200 matches of self-play with zero
    violations - while being completely unusable, since rendering the board is what
    the entire human interface does.
    """
    def explodes(state, viewer):
        raise AttributeError("Viewer has no attribute 'human'")

    assert "describe_state_renders_for_every_viewer" in checks(countdown(describe_state=explodes))


def test_a_module_returning_empty_text_is_caught() -> None:
    """Not raising is not the same as working."""
    module = countdown(describe_state=lambda s, v: "   ")
    assert "describe_state_renders_for_every_viewer" in checks(module)


def test_a_view_that_breaks_only_later_in_a_match_is_still_caught() -> None:
    """Rendering is re-checked periodically, not just at the opening position - a
    board that only breaks once it fills up is the more likely failure."""
    def breaks_later(state, viewer):
        if state["n"] < 50:  # fine at the opening, broken from ply 11 onwards
            raise RuntimeError("only fails once the board has filled")
        return "fine"

    module = countdown(
        initial_state=lambda seed=None: {TO_PLAY: "a", TURN: 0, PLY: 0, "n": 60},
        describe_state=breaks_later,
    )
    assert "describe_state_renders_for_every_viewer" in checks(module, max_plies=80)


def test_rendering_is_sampled_not_checked_every_ply() -> None:
    """The interval is a real trade, so it is stated rather than assumed.

    Checking every ply would triple validation cost to re-render a board that changes
    little; checking only the opening position is what missed the Phase 4 bug. Periodic
    sampling catches breakage that appears as the board fills, but a fault confined to
    the handful of plies between samples can still slip through - which is why
    `tests/test_views.py` exercises rendering directly as well.
    """
    from automa.core.selfplay import VIEW_CHECK_EVERY

    assert 1 < VIEW_CHECK_EVERY <= 50

    rendered: list[int] = []

    def counting(state, viewer):
        rendered.append(state["ply"])
        return "fine"

    module = countdown(
        initial_state=lambda seed=None: {TO_PLAY: "a", TURN: 0, PLY: 0, "n": 60},
        describe_state=counting,
    )
    random_playout(module, seed=0, max_plies=80)
    sampled = sorted(set(rendered))
    assert sampled[0] == 0, "the opening position is always rendered"
    assert len(sampled) > 1, "and it is re-checked as the match goes on"
