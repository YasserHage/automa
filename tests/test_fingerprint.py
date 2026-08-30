"""Comparing modules that share no internal representation. No model involved.

The fingerprint is how this project answers `plan.md`'s "functionally equivalent", which
cannot be checked literally between two modules with different state schemas. So the
question these tests have to settle is not "does it run" but **can it tell two games
apart** - and specifically, can it catch a module that is internally consistent and still
wrong, which is the realistic compiler failure.

A module whose `legal_actions` and `apply` disagree never reaches the fingerprint at all:
self-play catches it first and more cheaply. That division is asserted here too, because
it is the reason the gates are ordered the way they are.
"""

from __future__ import annotations

import types

import pytest

from automa.compiler import fingerprint as fp
from automa.core.game import settle
from automa.core.loader import load_game
from automa.core.selfplay import random_playout

DNUP = load_game("dnup")
NIM = load_game("nim")


def clone(module, **overrides):
    """A copy of a game module with some functions replaced."""
    fake = types.ModuleType(overrides.pop("NAME", f"{module.NAME}_variant"))
    fake.NAME = fake.__name__
    for attr in dir(module):
        if not attr.startswith("__"):
            setattr(fake, attr, getattr(module, attr))
    fake.NAME = fake.__name__
    for key, value in overrides.items():
        setattr(fake, key, value)
    return fake


# -- it agrees with itself ------------------------------------------------


def test_a_module_fingerprints_the_same_as_itself() -> None:
    """Seeded, so this is exact rather than approximate."""
    a = fp.collect(DNUP, matches=8)
    b = fp.collect(DNUP, matches=8)
    assert fp.compare(a, b).ok
    assert a.to_dict() == b.to_dict()


def test_dnup_is_measured_correctly() -> None:
    """The measurements have to mean something before a comparison can."""
    f = fp.collect(DNUP, matches=8)
    assert f.action_kinds == ["add", "play", "rotate", "take"]
    assert f.component_count == 26
    assert f.terminal_rate == 1.0
    assert f.match_len_min > 20, "DNUP matches are long; a short one means it collapsed"


# -- it tells different games apart ---------------------------------------


def test_nim_and_dnup_are_not_confused() -> None:
    comparison = fp.compare(fp.collect(DNUP, matches=8), fp.collect(NIM, matches=8))
    assert not comparison.ok
    assert any("action kinds" in d for d in comparison.differences)
    assert any("component count" in d for d in comparison.differences)


def test_a_module_missing_an_action_kind_is_caught() -> None:
    """Guard the guard, and the case that matters most.

    This variant is *internally consistent* - it simply never offers `take`, and its
    `apply` therefore never receives one. Self-play passes it happily: nothing crashes,
    nothing is lost, every match terminates. It is a different game, and the fingerprint
    is the only gate that can say so.
    """
    def no_take(state):
        return [a for a in DNUP.legal_actions(state) if a["kind"] != "take"]

    crippled = clone(DNUP, legal_actions=no_take, NAME="dnup_no_take")

    # It really is sound - which is the point.
    assert random_playout(crippled, seed=0).ok

    comparison = fp.compare(fp.collect(DNUP, matches=8), fp.collect(crippled, matches=8))
    assert not comparison.ok
    assert any("action kinds" in d for d in comparison.differences)


def test_a_module_with_the_wrong_deck_is_caught() -> None:
    """Right rules, wrong components - the player-count trim missed, say."""
    def short_deal(seed=None):
        # Settled first: DNUP now opens by asking the table what was dealt, so the
        # hands to trim only exist once that question has been answered.
        state = settle(DNUP, DNUP.initial_state(seed))
        hands = {p: state["hands"][p][:-2] for p in DNUP.PLAYERS}
        return {**state, "hands": hands}

    thin = clone(DNUP, initial_state=short_deal, NAME="dnup_thin")
    comparison = fp.compare(fp.collect(DNUP, matches=6), fp.collect(thin, matches=6))
    assert not comparison.ok
    assert any("component count" in d for d in comparison.differences)


def test_a_module_that_ends_far_too_quickly_is_caught() -> None:
    def ends_at_once(state):
        return state["ply"] >= 2

    def winner(state):
        if not ends_at_once(state):
            raise ValueError("not terminal")
        return "automa"

    hasty = clone(DNUP, is_terminal=ends_at_once, winner=winner, NAME="dnup_hasty")
    comparison = fp.compare(fp.collect(DNUP, matches=6), fp.collect(hasty, matches=6))
    assert not comparison.ok
    assert any("match length" in d for d in comparison.differences)


# -- what the fingerprint is NOT for --------------------------------------


def test_an_inconsistent_module_is_caught_by_selfplay_not_the_fingerprint() -> None:
    """Ordering rationale, asserted rather than assumed.

    A module offering actions its own `apply` rejects is the cheap failure: self-play
    finds it in milliseconds. Reaching the fingerprint first would waste a full sweep to
    learn the same thing, which is why the gates run cheapest-first.
    """
    def offers_the_impossible(state):
        return [*DNUP.legal_actions(state), {"label": "impossible", "kind": "play", "cards": [999]}]

    broken = clone(DNUP, legal_actions=offers_the_impossible, NAME="dnup_broken")

    result = random_playout(broken, seed=0, max_plies=400)
    assert not result.ok, "self-play must catch a module whose actions its apply rejects"


def test_tolerance_absorbs_sampling_noise_but_not_a_different_game() -> None:
    """Random self-play is noisy; the tolerance has to be wide enough to survive that
    and narrow enough to still mean something."""
    base = fp.collect(DNUP, matches=6)

    nudged = fp.Fingerprint(**{**base.to_dict(), "match_len_median": base.match_len_median * 1.2})
    assert fp.compare(base, nudged).ok, "20% drift is noise, not a different game"

    doubled = fp.Fingerprint(**{**base.to_dict(), "match_len_median": base.match_len_median * 3})
    assert not fp.compare(base, doubled).ok, "3x is not noise"


def test_the_rendered_table_names_the_differences() -> None:
    """A person reads this, so it has to say what differs rather than just failing."""
    a, b = fp.collect(DNUP, matches=6), fp.collect(NIM, matches=6)
    text = fp.render(a, b, fp.compare(a, b))
    assert "DIFFERENT GAME" in text
    assert "action kinds" in text
    assert "dnup" in text and "nim" in text


def test_component_count_is_absent_rather_than_wrong_for_a_pieceless_game() -> None:
    """Nim has no components shaped like cards. Reporting None is honest; reporting 0
    would look like a game that lost all its pieces."""
    assert fp.collect(NIM, matches=4).component_count is None


@pytest.mark.parametrize("game", [DNUP, NIM], ids=lambda m: m.NAME)
def test_the_state_schema_report_is_readable(game) -> None:
    """Printed at the approval gate, because golden cases must be authored against
    whatever schema the compiler chose."""
    text = fp.state_key_report(game)
    assert game.NAME in text
    assert "to_play" in text
    assert "legal actions" in text
