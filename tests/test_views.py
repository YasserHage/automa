"""The privacy property: no view reveals what its viewer may not see.

"The tactician never sees what it shouldn't" is one of the architecture's load-bearing
claims, and until something checks it, it is only a sentence in a README.

The property is deliberately **asymmetric**, because a locked decision makes Automa's
hand open to the person at the table - they need it to run Automa's side physically,
and it is what makes every move auditable. So the claim is not "neither player sees
the other's hand". It is: the human's hand is theirs alone, Automa's hand is public,
and the seed the deal was derived from is nobody's.
"""

from __future__ import annotations

from types import ModuleType

import pytest

from automa.core.game import AUTOMA, HUMAN, Viewer
from automa.core.loader import available_games, load_game
from automa.games import dnup

#: Distinctive enough that finding it in a rendered view cannot be a coincidence.
SEED = 987654321


@pytest.fixture(params=available_games())
def game(request: pytest.FixtureRequest) -> ModuleType:
    return load_game(request.param)


def test_every_viewer_gets_something_to_read(game: ModuleType) -> None:
    state = game.initial_state(SEED)
    for viewer in Viewer:
        assert game.describe_state(state, viewer).strip()


def test_no_view_leaks_the_seed(game: ModuleType) -> None:
    """The deal is a function of the seed, so printing it would print the deal."""
    state = game.initial_state(SEED)
    for viewer in Viewer:
        assert str(SEED) not in game.describe_state(state, viewer)


def test_nim_shows_everyone_the_same_thing() -> None:
    """Perfect information, and the test says so rather than leaving it implied."""
    nim = load_game("nim")
    state = nim.initial_state(SEED)
    rendered = {nim.describe_state(state, viewer) for viewer in Viewer}
    assert len(rendered) == 1


# -- DNUP, where hidden information actually exists ------------------------


def _card_text(card: dict) -> str:
    return f"{card['up']}({card['down']})"


def _views(state: dict) -> dict[Viewer, str]:
    return {viewer: dnup.describe_state(state, viewer) for viewer in Viewer}


def test_the_humans_hand_is_visible_only_to_the_human() -> None:
    state = dnup.initial_state(SEED)
    views = _views(state)

    # A card's rendering could legitimately appear because an identical pair sits in
    # Automa's open hand, so compare only the renderings unique to the human.
    elsewhere = {_card_text(c) for c in state["hands"][AUTOMA]}
    private = {_card_text(c) for c in state["hands"][HUMAN]} - elsewhere
    assert private, "the fixture must actually contain hidden cards"

    for text in private:
        assert text not in views[Viewer.PUBLIC]
        assert text not in views[Viewer.AUTOMA]
        assert text in views[Viewer.HUMAN]


def test_the_public_view_still_says_how_many_cards_are_hidden() -> None:
    """Counts are public knowledge and deduction memory needs them; values are not."""
    state = dnup.initial_state(SEED)
    public = dnup.describe_state(state, Viewer.PUBLIC)
    assert "hidden" in public
    assert str(len(state["hands"][HUMAN])) in public


def test_automas_hand_is_open_to_everyone() -> None:
    state = dnup.initial_state(SEED)
    views = _views(state)
    for card in state["hands"][AUTOMA]:
        for viewer in Viewer:
            assert _card_text(card) in views[viewer], (card, viewer)


def test_cards_on_the_table_are_visible_to_everyone() -> None:
    """Both values are printed on a face-up card, so nothing about it is secret."""
    from test_dnup import build

    state = build(human_areas=(((1, 2), (4, 2)), ()), automa_hand=((0, 1),))
    for viewer in Viewer:
        text = dnup.describe_state(state, viewer)
        assert "2(5)" in text and "2(9)" in text
