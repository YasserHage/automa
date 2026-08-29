"""DNUP: the mechanics, on hand-built positions.

The self-play harness proves the module never crashes and never loses a card. It
cannot prove the module implements *DNUP* rather than some other consistent game -
only positions with a known right answer can do that. These are those positions,
concentrating on the rules that are easy to get subtly wrong: the displacement rule,
the fact that your own sets can conflict with each other, the rotation that happens
whenever a card leaves the table, and the delayed discard.
"""

from __future__ import annotations

import pytest

from automa.core.game import AUTOMA, HUMAN, PLY, TO_PLAY, TURN
from automa.core.selfplay import random_playout
from automa.games import dnup

Spec = tuple[int, int]  # (card id, which of its two values is face up)


def card(card_id: int, up: int) -> dict:
    """Build a card in a stated orientation, refusing values it was never printed with."""
    a, b = dnup.DECK[card_id]
    assert up in (a, b), f"card {card_id} is {a}/{b}, not {up}"
    return {"id": card_id, "up": up, "down": b if up == a else a}


def build(
    *,
    automa_hand: tuple[Spec, ...] = (),
    human_hand: tuple[Spec, ...] = (),
    automa_areas: tuple[tuple[Spec, ...], ...] = ((), ()),
    human_areas: tuple[tuple[Spec, ...], ...] = ((), ()),
    to_play: str = AUTOMA,
    area: int = 0,
    rounds_won: dict[str, int] | None = None,
) -> dict:
    """A valid state with the named cards placed and every other card in the discard.

    Parking the remainder in the discard keeps component conservation true, so
    `check_invariants` stays meaningful on a hand-built position.
    """
    hands = {
        AUTOMA: [card(*s) for s in automa_hand],
        HUMAN: [card(*s) for s in human_hand],
    }
    areas = {
        AUTOMA: [[card(*s) for s in a] for a in automa_areas],
        HUMAN: [[card(*s) for s in a] for a in human_areas],
    }
    placed = {c["id"] for group in (*hands.values(), *areas[AUTOMA], *areas[HUMAN]) for c in group}
    discard = [
        {"id": i, "up": a, "down": b}
        for i, (a, b) in enumerate(dnup.DECK)
        if i not in placed
    ]
    return {
        TO_PLAY: to_play,
        TURN: 0,
        PLY: 0,
        "seed": 0,
        "round": 1,
        "area": area,
        "rounds_won": rounds_won or {AUTOMA: 0, HUMAN: 0},
        "hands": hands,
        "areas": areas,
        "discard": discard,
    }


def plays(state: dict) -> list[dict]:
    return [a for a in dnup.legal_actions(state) if a["kind"] == "play"]


def test_the_helper_builds_states_the_module_considers_sound() -> None:
    assert dnup.check_invariants(build(automa_hand=((10, 3),))) == []


# -- the deck and the deal -------------------------------------------------


def test_deck_matches_the_physical_two_player_deck() -> None:
    """Pinned to the transcribed card list, so an edit to DECK cannot pass unnoticed.

    The skew is the interesting part and worth asserting rather than assuming: high
    values are scarce, which is what makes a high set worth holding, and there is
    exactly one 10 in the deck - so a value-10 set can never exceed a single card.
    """
    assert len(dnup.DECK) == 26
    counts: dict[int, int] = {}
    for a, b in dnup.DECK:
        counts[a] = counts.get(a, 0) + 1
        counts[b] = counts.get(b, 0) + 1
    assert counts == {1: 7, 2: 7, 3: 6, 4: 6, 5: 6, 6: 6, 7: 6, 8: 4, 9: 3, 10: 1}
    assert sum(counts.values()) == 2 * len(dnup.DECK)


def test_the_deal_splits_the_whole_deck_in_half() -> None:
    state = dnup.initial_state(3)
    assert len(state["hands"][AUTOMA]) == len(state["hands"][HUMAN]) == len(dnup.DECK) // 2
    assert dnup.check_invariants(state) == []


def test_the_star_card_holder_opens_with_their_second_play_area() -> None:
    """The opening asymmetry is in the rules, not an artefact of the implementation."""
    for seed in range(20):
        state = dnup.initial_state(seed)
        opener = state[TO_PLAY]
        assert any(c["id"] == dnup.STAR_CARD_ID for c in state["hands"][opener])
        assert state["area"] == 1


def test_turn_order_runs_both_of_a_players_areas_before_the_opponents() -> None:
    state = dnup.initial_state(1)
    first = state[TO_PLAY]
    seats = [(state[TO_PLAY], state["area"])]
    for _ in range(4):
        rotate = next(a for a in dnup.legal_actions(state) if a["kind"] == "rotate")
        state = dnup.apply(state, rotate)
        seats.append((state[TO_PLAY], state["area"]))
    other = HUMAN if first == AUTOMA else AUTOMA
    # The opener's single first turn, then the opponent's pair, then the opener's pair.
    assert seats == [(first, 1), (other, 0), (other, 1), (first, 0), (first, 1)]


# -- the four actions ------------------------------------------------------


def test_rotating_your_hand_swaps_every_card() -> None:
    state = build(automa_hand=((10, 3), (0, 1)))
    rotate = next(a for a in dnup.legal_actions(state) if a["kind"] == "rotate")
    after = dnup.apply(state, rotate)
    assert [(c["up"], c["down"]) for c in after["hands"][AUTOMA]] == [(8, 3), (5, 1)]
    assert after[PLY] == state[PLY] + 1
    assert after[TURN] == state[TURN] + 1


def test_taking_a_set_rotates_it_into_your_hand() -> None:
    state = build(automa_hand=((0, 1),), human_areas=(((1, 2), (4, 2)), ()))
    take = next(a for a in dnup.legal_actions(state) if a["kind"] == "take")
    after = dnup.apply(state, take)
    assert after["areas"][HUMAN][0] == []
    # Cards 1 and 4 are printed 2/5 and 2/9, so taking them turns them into a 5 and a 9.
    assert [(c["up"], c["down"]) for c in after["hands"][AUTOMA]] == [(1, 5), (5, 2), (9, 2)]


def test_playing_a_higher_set_of_the_same_size_sends_the_other_one_back_rotated() -> None:
    state = build(
        automa_hand=((10, 3), (18, 3), (0, 1)),
        human_areas=(((1, 2), (4, 2)), ()),
    )
    two_threes = next(a for a in plays(state) if len(a["cards"]) == 2)
    after = dnup.apply(state, two_threes)

    assert [c["id"] for c in after["areas"][AUTOMA][0]] == [10, 18]
    assert after["areas"][HUMAN][0] == []
    # The beaten set returns to its owner rotated: 2/5 becomes a 5, 2/9 becomes a 9.
    assert sorted((c["up"], c["down"]) for c in after["hands"][HUMAN]) == [(5, 2), (9, 2)]
    assert dnup.check_invariants(after) == []


def test_a_set_may_not_be_played_unless_it_beats_the_same_size_set() -> None:
    state = build(
        automa_hand=((10, 3), (18, 3), (0, 1)),
        human_areas=(((2, 5), (12, 5)), ()),  # size 2, value 5
    )
    assert not [a for a in plays(state) if len(a["cards"]) == 2]
    # A one-card set is unaffected - the restriction is per size, not global.
    assert [a for a in plays(state) if len(a["cards"]) == 1]


def test_your_own_two_sets_conflict_with_each_other() -> None:
    """The two-player rules say so explicitly, and it is the easiest rule to miss."""
    state = build(
        automa_hand=((16, 2), (0, 1)),
        automa_areas=((), ((7, 1), (11, 1))),  # your own size-2 set, value 1
        human_areas=(((1, 2),), ()),  # their size-1 set, value 2
        area=0,
    )
    add = next(a for a in dnup.legal_actions(state) if a["kind"] == "add")
    after = dnup.apply(state, add)

    assert len(after["areas"][HUMAN][0]) == 2
    assert after["areas"][AUTOMA][1] == [], "your own set should have been displaced"
    # Cards 7 and 11 are printed 1/6 and 1/7, so they come back as a 6 and a 7.
    assert sorted((c["up"], c["down"]) for c in after["hands"][AUTOMA]) == [
        (1, 5),
        (6, 1),
        (7, 1),
    ]


def test_adding_is_blocked_when_the_enlarged_set_would_not_win_its_size() -> None:
    state = build(
        automa_hand=((16, 2), (0, 1)),
        automa_areas=((), ((2, 5), (12, 5))),  # size 2, value 5
        human_areas=(((1, 2),), ()),
        area=0,
    )
    assert not [a for a in dnup.legal_actions(state) if a["kind"] == "add"]


def test_added_cards_must_match_the_sets_value() -> None:
    state = build(automa_hand=((10, 3),), human_areas=(((1, 2),), ()))
    with pytest.raises(ValueError):
        dnup.apply(state, {"kind": "add", "area": 0, "card": 10})


def test_rotating_your_hand_is_always_available() -> None:
    """This is what guarantees a non-terminal state always offers a legal action."""
    for state in (build(), build(automa_hand=((10, 3),)), dnup.initial_state(2)):
        assert any(a["kind"] == "rotate" for a in dnup.legal_actions(state))


# -- turn structure and the end of things ----------------------------------


def test_a_played_set_survives_until_that_areas_turn_comes_round_again() -> None:
    state = build(automa_hand=((10, 3), (0, 1)), human_hand=((18, 3), (7, 1)), area=0)
    state = dnup.apply(state, next(a for a in plays(state) if a["cards"] == [10]))
    assert [c["id"] for c in state["areas"][AUTOMA][0]] == [10]

    # Three turns pass - the opener's other area, then both of the opponent's.
    for _ in range(3):
        assert state["areas"][AUTOMA][0], "the set should still be on the table"
        rotate = next(a for a in dnup.legal_actions(state) if a["kind"] == "rotate")
        state = dnup.apply(state, rotate)

    assert (state[TO_PLAY], state["area"]) == (AUTOMA, 0)
    assert state["areas"][AUTOMA][0] == [], "the set is discarded as that area's turn begins"
    assert any(c["id"] == 10 for c in state["discard"])


def test_emptying_your_hand_wins_the_round_and_the_next_one_is_dealt() -> None:
    state = build(automa_hand=((10, 3),), human_hand=((18, 3),))
    after = dnup.apply(state, plays(state)[0])

    assert after["rounds_won"][AUTOMA] == 1
    assert after["round"] == 2
    assert not dnup.is_terminal(after)
    assert len(after["hands"][AUTOMA]) == len(after["hands"][HUMAN]) == len(dnup.DECK) // 2
    assert after["discard"] == [] and after["areas"][AUTOMA] == [[], []]
    assert dnup.check_invariants(after) == []


def test_a_second_round_win_ends_the_match() -> None:
    state = build(
        automa_hand=((10, 3),),
        human_hand=((18, 3),),
        rounds_won={AUTOMA: 1, HUMAN: 0},
    )
    after = dnup.apply(state, plays(state)[0])

    assert dnup.is_terminal(after)
    assert dnup.winner(after) == AUTOMA
    assert dnup.legal_actions(after) == []
    with pytest.raises(ValueError):
        dnup.apply(after, {"kind": "rotate"})


def test_winner_needs_a_finished_match() -> None:
    with pytest.raises(ValueError):
        dnup.winner(dnup.initial_state(0))


# -- what check_invariants is for ------------------------------------------


def test_check_invariants_catches_a_lost_card() -> None:
    state = build(automa_hand=((10, 3), (18, 3)))
    state["hands"][AUTOMA].pop()
    assert any("conservation" in p for p in dnup.check_invariants(state))


def test_check_invariants_catches_a_card_showing_a_value_it_never_had() -> None:
    state = build(automa_hand=((10, 3),))
    state["hands"][AUTOMA][0]["up"] = 9
    assert any("printed" in p for p in dnup.check_invariants(state))


def test_check_invariants_catches_two_sets_of_the_same_size() -> None:
    """The displacement rule should make this unreachable; assert it stays unreachable."""
    state = build(automa_areas=(((10, 3),), ()), human_areas=(((1, 2),), ()))
    assert any("share a size" in p for p in dnup.check_invariants(state))


def test_random_matches_never_put_two_sets_of_one_size_on_the_table() -> None:
    for seed in range(10):
        assert random_playout(dnup, seed=seed).ok
