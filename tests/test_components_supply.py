"""Playing with the cards that are actually on the table.

The end-to-end claim of this seam: a person reads out what was dealt, the module plays
with exactly that, and nothing about the deal counts as a decision or a correction. DNUP
is the game under test because it deals every round rather than only at the start, so a
match exercises the path repeatedly rather than once.

The other half of the claim matters just as much and is asserted here too: with nobody to
ask, every module still deals itself the same seeded hand it always did. That is what
keeps self-play, the fingerprint and the eval battery meaningful.
"""

from __future__ import annotations

from automa.core.game import (
    AUTOMA,
    COMPONENTS,
    HUMAN,
    KIND_CARD_EFFECT,
    KIND_COMPONENTS,
    SLOT,
    UNAVAILABLE,
    UNKNOWN,
    settle,
)
from automa.core.match import compose, play_match, random_decider, uninformed
from automa.games import dnup
from automa.table import table_informant


def as_typed(cards: list[dict]) -> str:
    """A hand, written the way somebody at the table would read it out."""
    return " ".join(f"{c['up']}/{c['down']}" for c in cards)


def deciders() -> dict:
    return {p: random_decider(3) for p in dnup.PLAYERS}


def a_table(first_seed: int = 100):
    """Somebody reading out a coherent deal, one hand at a time, round after round."""
    asked: list[str] = []

    def ask(question) -> str:
        asked.append(question.prompt)
        deal = dnup._deal(first_seed + (len(asked) - 1) // 2, 1)
        return as_typed(deal[AUTOMA if len(asked) % 2 else HUMAN])

    return ask, asked


# -- what the table says is what gets played -------------------------------


def test_the_supplied_cards_are_the_ones_played_with() -> None:
    """Not the module's shuffle - the deal that happened in front of the person."""
    theirs = dnup._deal(99, 1)  # a deal from some other seed, standing in for the table
    lines = [as_typed(theirs[AUTOMA]), as_typed(theirs[HUMAN])]

    state = dnup.initial_state(1)
    for line in lines:
        answer = table_informant(lambda _q, text=line: text)(None, state[UNKNOWN])
        state = dnup.resolve_unknown(state, answer)

    assert UNKNOWN not in state
    for player in dnup.PLAYERS:
        assert as_typed(state["hands"][player]) == as_typed(theirs[player])
    assert dnup.check_invariants(state) == []
    # A different seed was passed in, and it changed nothing.
    assert state["hands"] != settle(dnup, dnup.initial_state(1))["hands"]


def test_orientation_is_taken_as_it_was_read_out() -> None:
    """Which value is upright is the hidden state DNUP turns on, so it is not guessed."""
    state = dnup.initial_state(1)
    hand = ["5/1"] + [f"{a}/{b}" for a, b in dnup.DECK[1:13]]
    state = dnup.resolve_unknown(state, {SLOT: "hands.automa", COMPONENTS: hand})

    star = next(c for c in state["hands"][AUTOMA] if c["id"] == dnup.STAR_CARD_ID)
    assert (star["up"], star["down"]) == (5, 1), "the deck prints it 1/5; it was read 5/1"


def test_whoever_holds_the_star_card_opens_the_round() -> None:
    """Read off the answer rather than asked for separately - one less question."""
    state = dnup.initial_state(1)
    without_star = [f"{a}/{b}" for a, b in dnup.DECK[1:14]]
    state = dnup.resolve_unknown(state, {SLOT: "hands.automa", COMPONENTS: without_star})
    state = dnup.resolve_unknown(
        state, {SLOT: "hands.human", COMPONENTS: ["1/5"] + [f"{a}/{b}" for a, b in dnup.DECK[14:]]}
    )
    assert state["to_play"] == HUMAN
    assert state["area"] == 1


def test_a_card_that_is_not_in_the_deck_is_reported_not_refused() -> None:
    """Minimal validation, and the mistake surfaces where a person can act on it."""
    state = dnup.initial_state(1)
    typo = ["9/9"] + [f"{a}/{b}" for a, b in dnup.DECK[1:13]]
    state = dnup.resolve_unknown(state, {SLOT: "hands.automa", COMPONENTS: typo})
    state = settle(dnup, state)

    assert len(state["hands"][AUTOMA]) == 13, "the hand is played with, not rejected"
    problems = dnup.check_invariants(state)
    assert any("9/9" in p for p in problems)


# -- with nobody to ask ----------------------------------------------------


def test_nothing_supplied_deals_exactly_what_it_always_dealt() -> None:
    """The offline path, and the reason every existing seeded fixture still holds."""
    for seed in range(5):
        state = settle(dnup, dnup.initial_state(seed))
        assert state["hands"] == dnup._deal(seed, 1)


def test_one_hand_supplied_and_the_other_not_still_conserves_the_deck() -> None:
    """The seeded half would collide, so what is left of the deck is dealt instead."""
    state = dnup.initial_state(2)
    mine = [f"{a}/{b}" for a, b in dnup.DECK[:13]]
    state = dnup.resolve_unknown(state, {SLOT: "hands.automa", COMPONENTS: mine})
    state = dnup.resolve_unknown(state, {SLOT: "hands.human", UNAVAILABLE: True})

    assert len(state["hands"][HUMAN]) == 13
    assert dnup.check_invariants(state) == []


# -- through the match loop ------------------------------------------------


def test_a_whole_match_can_be_played_from_supplied_deals() -> None:
    ask, asked = a_table()
    result = play_match(
        dnup, deciders(), seed=4, informant=table_informant(ask), max_plies=400
    )

    assert result.outcome == "terminal"
    assert len(asked) >= 2, "at least the opening round was read off the table"
    assert result.informed == len(asked)
    assert result.invariant_violations == [], "a deal read off the table conserves the deck"


def test_being_told_what_was_dealt_is_not_a_correction() -> None:
    """The primary metric counts interventions. A fact is not one of them."""
    result = play_match(
        dnup,
        deciders(),
        seed=4,
        informant=table_informant(a_table(7)[0]),
        max_plies=400,
    )
    assert result.informed > 0
    assert sum(result.interventions.values()) == 0
    assert sum(result.fallbacks.values()) == 0


def test_an_unattended_match_needs_no_one_at_the_table() -> None:
    result = play_match(dnup, deciders(), seed=4, informant=uninformed, max_plies=400)
    assert result.outcome == "terminal"


def test_the_deal_is_on_the_record_as_information(tmp_path) -> None:
    from automa.trajectory import TrajectoryWriter

    with TrajectoryWriter("dnup", directory=tmp_path) as traj:
        play_match(
            dnup,
            deciders(),
            seed=4,
            trajectory=traj,
            informant=table_informant(a_table(7)[0]),
            max_plies=400,
        )
        path = traj.path

    told = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if '"table_information"' in line
    ]
    assert told, "a fact supplied by the table must be logged"
    assert all(KIND_COMPONENTS in line for line in told)


# -- composing the two sources ---------------------------------------------


def one(kind: str, answer: dict):
    def _informant(_ctx, unknown):
        return dict(answer) if unknown.get("kind") == kind else {**unknown, UNAVAILABLE: True}

    return _informant


def test_each_source_answers_only_what_it_knows() -> None:
    both = compose(
        one(KIND_COMPONENTS, {SLOT: "hands.automa", COMPONENTS: ["1/5"]}),
        one(KIND_CARD_EFFECT, {"card_type": "wasabi", "effects": []}),
    )
    components = both(None, {"kind": KIND_COMPONENTS, SLOT: "hands.automa", "prompt": "?"})
    effect = both(None, {"kind": KIND_CARD_EFFECT, "card_type": "wasabi", "prompt": "?"})

    assert components[COMPONENTS] == ["1/5"]
    assert effect["effects"] == []


def test_the_first_real_answer_wins() -> None:
    both = compose(
        one(KIND_COMPONENTS, {SLOT: "a", COMPONENTS: ["first"]}),
        one(KIND_COMPONENTS, {SLOT: "a", COMPONENTS: ["second"]}),
    )
    assert both(None, {"kind": KIND_COMPONENTS, SLOT: "a", "prompt": "?"})[COMPONENTS] == ["first"]


def test_a_question_nobody_can_answer_comes_back_unavailable() -> None:
    """Not an error: the module's own fallback is what happens next."""
    both = compose(one(KIND_CARD_EFFECT, {"effects": []}))
    answer = both(None, {"kind": KIND_COMPONENTS, SLOT: "a", "prompt": "?"})
    assert answer == {SLOT: "a", UNAVAILABLE: True}
