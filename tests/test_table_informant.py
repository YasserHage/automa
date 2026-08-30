"""The table informant: what the person reads off the table, and the one check on it.

The count is the whole of the validation, and these tests are what keep it that way -
both halves matter. Too little checking and a miscounted hand reaches the module as a
game with 12 cards in it; too much and a card name nobody has taught the system yet gets
rejected at the one moment it should be *learned*, which is the case the product exists
for.
"""

from __future__ import annotations

from automa.core.game import (
    COMPONENTS,
    COUNT,
    KIND_CARD_EFFECT,
    KIND_COMPONENTS,
    OPTIONS,
    SLOT,
    UNAVAILABLE,
)
from automa.table import Question, split_components, table_informant


def question(**extra) -> dict:
    return {"kind": KIND_COMPONENTS, SLOT: "hands.automa", COUNT: 3,
            "prompt": "read out three cards", **extra}


def scripted(*answers: str | None):
    """An asker that reads from a script, recording the questions it was put."""
    asked: list[Question] = []
    remaining = list(answers)

    def ask(q: Question) -> str | None:
        asked.append(q)
        return remaining.pop(0) if remaining else None

    return ask, asked


def test_what_the_person_typed_reaches_the_module() -> None:
    ask, _ = scripted("tempura, sashimi, maki_2")
    answer = table_informant(ask)(None, question())
    assert answer == {SLOT: "hands.automa", COMPONENTS: ["tempura", "sashimi", "maki_2"]}


def test_the_wrong_number_of_cards_is_put_back_to_the_person() -> None:
    """The only validation there is, and the module never sees the miscount."""
    ask, asked = scripted("1/5 2/9", "1/5 2/9 3/4")
    answer = table_informant(ask)(None, question())

    assert answer[COMPONENTS] == ["1/5", "2/9", "3/4"]
    assert asked[0].note is None
    assert "2 component(s)" in asked[1].note and "3" in asked[1].note


def test_giving_up_after_repeated_miscounts_is_an_answer_not_a_hang() -> None:
    ask, asked = scripted("1/5", "1/5", "1/5", "1/5")
    answer = table_informant(ask, attempts=3)(None, question())
    assert answer[UNAVAILABLE] is True
    assert len(asked) == 3


def test_saying_nothing_is_always_permitted() -> None:
    """An empty line means 'I cannot say', and the module deals its own."""
    ask, asked = scripted(None)
    answer = table_informant(ask)(None, question())
    assert answer == {SLOT: "hands.automa", UNAVAILABLE: True}
    assert len(asked) == 1, "nobody should be asked twice for saying they cannot say"


def test_a_name_the_game_never_heard_of_is_passed_through() -> None:
    """Not an error. For a game whose cards are learned, this is how one arrives."""
    ask, _ = scripted("tempura, wasabi, something_new")
    answer = table_informant(ask)(None, question(**{OPTIONS: ["tempura", "wasabi"]}))
    assert answer[COMPONENTS] == ["tempura", "wasabi", "something_new"]


def test_the_options_are_offered_as_a_hint() -> None:
    ask, asked = scripted("a b c")
    table_informant(ask)(None, question(**{OPTIONS: ["a", "b", "c", "d"]}))
    assert asked[0].options == ("a", "b", "c", "d")
    assert asked[0].count == 3


def test_a_question_of_another_kind_is_declined_rather_than_guessed_at() -> None:
    """It composes with the catalog informant instead of competing with it."""
    ask, asked = scripted("shouldn't be reached")
    answer = table_informant(ask)(
        None, {"kind": KIND_CARD_EFFECT, "card_type": "wasabi", "prompt": "what is it?"}
    )
    assert answer == {"card_type": "wasabi", UNAVAILABLE: True}
    assert asked == []


def test_a_question_without_a_count_takes_whatever_arrives() -> None:
    ask, _ = scripted("4")
    answer = table_informant(ask)(
        None, {"kind": KIND_COMPONENTS, SLOT: "die", "prompt": "what did the die show?"}
    )
    assert answer == {SLOT: "die", COMPONENTS: ["4"]}


def test_the_identifying_fields_come_back_so_answers_can_be_told_apart() -> None:
    answer = table_informant(scripted("a b c")[0])(None, question(round=2))
    assert answer[SLOT] == "hands.automa" and answer["round"] == 2
    assert "prompt" not in answer and COUNT not in answer


class TestSplitting:
    """How a line becomes tokens. Shallow on purpose - nothing here corrects anybody."""

    def test_commas_win_where_they_appear_because_a_name_can_contain_a_space(self) -> None:
        assert split_components("salmon nigiri, maki 2") == ["salmon_nigiri", "maki_2"]

    def test_whitespace_separates_when_there_are_no_commas(self) -> None:
        assert split_components("1/5  2/9\t3/4") == ["1/5", "2/9", "3/4"]

    def test_case_and_stray_spacing_do_not_survive(self) -> None:
        assert split_components(" Tempura ,  SASHIMI ") == ["tempura", "sashimi"]

    def test_empty_pieces_are_dropped_rather_than_counted(self) -> None:
        assert split_components("a,,b,") == ["a", "b"]
