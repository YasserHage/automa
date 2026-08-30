"""`count_field` is the one string a catalog and a module must agree on.

Every other part of an effect is checked structurally: an unknown operation is rejected
by the vocabulary, and an operation the module does not implement is rejected by
`unsupported`. `count_field` slipped between both, because its *value* is free text
naming a quantity the module derives rather than a count of card copies.

The consequence when they disagree is the reason this file exists. A catalog written
with `count_field: "maki_icons"` against a Sushi Go module that tested for `"icons"` did
not raise: the module fell through to counting copies of a single card type, and because
the fallthrough scanned players in order, it favoured the first one. Four hundred random
matches came out 293-106 instead of roughly even. Nothing was logged, and the score
looked entirely plausible.
"""

from __future__ import annotations

from automa.catalog import store
from automa.catalog.vocabulary import describe_terms, unsupported

MAKI = [{"op": "score_majority", "first": 6, "second": 3, "count_field": "icons"}]
OPS = {"score_majority", "score_flat"}


def test_a_count_field_the_module_does_not_derive_is_rejected():
    problems = unsupported(
        [{"op": "score_majority", "first": 6, "count_field": "maki_icons"}],
        supported=OPS,
        terms={"count_field": {"icons"}},
    )
    assert problems
    assert "maki_icons" in problems[0]
    assert "icons" in problems[0], "the message must say what the game does know"


def test_a_count_field_the_module_derives_is_accepted():
    assert unsupported(MAKI, supported=OPS, terms={"count_field": {"icons"}}) == []


def test_no_declaration_means_no_check():
    """Same contract as `supported`: a module that does not declare restricts nothing."""
    assert unsupported(MAKI, supported=OPS, terms=None) == []


def test_omitting_count_field_is_always_fine():
    """Absent means 'count copies of this card', which every game can do."""
    effects = [{"op": "score_flat", "points": 2}]
    assert unsupported(effects, supported=OPS, terms={"count_field": set()}) == []


def test_a_game_that_derives_nothing_rejects_every_count_field():
    problems = unsupported(MAKI, supported=OPS, terms={"count_field": set()})
    assert problems and "icons" in problems[0]


def test_the_catalog_refuses_to_store_a_disagreement():
    """The check has to bite where entries are written, not only where they are read."""
    catalog = store.Catalog(game="sushigo")
    problems = catalog.learn(
        "maki_2",
        effects=[{"op": "score_majority", "first": 6, "count_field": "maki_icons"}],
        description="most maki icons scores 6",
        supported=OPS,
        terms={"count_field": {"icons"}},
    )
    assert problems and "maki_icons" in problems[0]
    assert "maki_2" not in catalog, "a rejected entry must not be stored"


def test_the_catalog_stores_an_agreement():
    catalog = store.Catalog(game="sushigo")
    assert catalog.learn(
        "maki_2", effects=MAKI, description="two icons", supported=OPS,
        terms={"count_field": {"icons"}},
    ) == []
    assert "maki_2" in catalog


def test_the_agent_is_told_which_quantities_exist():
    """Otherwise it is asked to name a derived quantity with nothing to name it from."""
    text = describe_terms({"count_field": {"icons", "coins"}})
    assert "coins" in text and "icons" in text


def test_declaring_an_empty_set_forbids_the_field():
    assert "uses none" in describe_terms({"count_field": set()})


def test_declaring_nothing_at_all_does_not_forbid_the_field():
    """Silence from a module is not permission to forbid the field.

    Reading `None` as "forbidden" drafted Sushi Go's maki cards with no counted
    quantity, which turned one majority contest over icons into three over card copies
    and lifted the median winning score from 49 to 61 without raising anything.
    """
    text = describe_terms(None)
    assert "uses none" not in text
    assert "cannot be checked" in text
    assert text != describe_terms({"count_field": set()})


def test_an_undeclared_name_falls_back_to_the_card_type():
    """What makes both sides agree when the module declares nothing.

    A module that gates a rule on a card names the marker after that card - which is
    what the compiler did for Sushi Go's chopsticks, unprompted. The cataloguer called
    it `extra_card_from_hand` instead and the mechanic stayed switched off.
    """
    text = describe_terms(None, card_type="chopsticks")
    assert "chopsticks" in text


def test_every_named_field_is_checked_not_only_count_field():
    """Checking one field and leaving three is how the second instance happened."""
    from automa.catalog.vocabulary import NAMED_FIELDS

    assert set(NAMED_FIELDS) == {"count_field", "flag", "resource", "category"}

    problems = unsupported(
        [{"op": "set_flag", "flag": "extra_card_from_hand"}],
        supported={"set_flag"},
        terms={"flag": {"chopsticks"}},
    )
    assert problems and "extra_card_from_hand" in problems[0]

    assert unsupported(
        [{"op": "set_flag", "flag": "chopsticks"}],
        supported={"set_flag"},
        terms={"flag": {"chopsticks"}},
    ) == []
