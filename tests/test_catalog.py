"""The card catalog and the effect vocabulary. No model anywhere in this file.

Invariant 5 says card effects are executable data structures, never prose interpreted at
runtime. These tests are what makes that checkable: the vocabulary is closed, an entry
that does not validate is refused rather than stored, and a card that cannot be expressed
is parked rather than approximated.

The parking behaviour matters more than it looks. The tempting alternative - treat an
un-expressible card as having no effect - would let the system play a silently different
game, which is the exact failure the whole project exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automa.catalog import store
from automa.catalog.vocabulary import (
    VOCABULARY,
    describe_vocabulary,
    unsupported,
    validate,
)

TEMPURA = [{"op": "score_per_set", "size": 2, "points": 5}]
MAKI = [{"op": "score_majority", "first": 6, "second": 3}]


# -- the vocabulary is closed ---------------------------------------------


def test_a_known_operation_validates() -> None:
    assert validate(TEMPURA) == []
    assert validate(MAKI) == []


def test_an_invented_operation_is_refused() -> None:
    """The one thing a model must never do is add a primitive at match time."""
    problems = validate([{"op": "summon_dragon", "power": 9}])
    assert problems
    assert "unknown operation" in problems[0]
    assert "closed" in problems[0]


def test_a_missing_required_field_is_refused() -> None:
    problems = validate([{"op": "score_per_set", "size": 2}])  # no points
    assert any("requires 'points'" in p for p in problems)


def test_an_unexpected_field_is_refused() -> None:
    """Silently ignoring it would mean the stored effect is not what was described."""
    problems = validate([{"op": "score_flat", "points": 1, "colour": "red"}])
    assert any("no field 'colour'" in p for p in problems)


def test_empty_effects_are_refused() -> None:
    """A card with genuinely no effect should say so, not arrive as an empty list."""
    assert validate([]) != []


def test_the_prompt_text_is_generated_from_the_definitions() -> None:
    """Restating the vocabulary in a prompt would let it drift from what validates."""
    text = describe_vocabulary()
    for name in VOCABULARY:
        assert name in text
    assert "complete" in text


# -- learning and refusing ------------------------------------------------


def test_learning_a_valid_card_makes_it_playable() -> None:
    catalog = store.Catalog(game="toy")
    assert catalog.learn("tempura", TEMPURA, "two of them score five") == []
    assert catalog.known() == {"tempura"}
    assert catalog.effects_for("tempura") == TEMPURA


def test_an_invalid_card_is_refused_rather_than_stored() -> None:
    """A catalog holding an unexecutable entry is worse than one that admits ignorance."""
    catalog = store.Catalog(game="toy")
    problems = catalog.learn("bad", [{"op": "nonsense"}])
    assert problems
    assert "bad" not in catalog
    assert catalog.known() == set()


def test_a_parked_card_is_neither_playable_nor_forgotten() -> None:
    """The escape case. Chopsticks is the real example: it changes how many cards you
    take, which no scoring operation can express."""
    catalog = store.Catalog(game="toy")
    catalog.park("chopsticks", "take two cards next turn", "changes turn structure")

    assert "chopsticks" in catalog
    assert catalog.parked() == {"chopsticks"}
    assert catalog.known() == set()
    assert catalog.effects_for("chopsticks") == [], "parked cards must not execute"


def test_a_parked_card_is_not_asked_about_again() -> None:
    """The person already answered. Asking twice would be rude, and it would corrupt
    the 'fewer questions the second time' measurement."""
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", TEMPURA)
    catalog.park("chopsticks", "take two", "turn structure")

    still_needed = catalog.unknown_among({"tempura", "chopsticks", "maki"})
    assert still_needed == {"maki"}


def test_a_module_receives_both_what_executes_and_what_was_asked() -> None:
    """Two different questions, and a module needs both answers.

    `effects` is what it *executes*; `described` is what it must not *ask about again*.
    Handing over only the first is a real bug the informant tests caught: a parked card
    would then be asked about on every single match, and the exit criterion compares
    question counts between matches.
    """
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", TEMPURA)
    catalog.park("chopsticks", "take two", "turn structure")

    data = catalog.as_state_data()
    assert set(data["effects"]) == {"tempura"}, "only playable cards execute"
    assert set(data["described"]) == {"tempura", "chopsticks"}, "but both were answered"
    assert catalog.described() == {"tempura", "chopsticks"}


# -- persistence ----------------------------------------------------------


def test_a_catalog_round_trips_through_disk(tmp_path: Path) -> None:
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", TEMPURA, "two score five")
    catalog.park("chopsticks", "take two", "turn structure")
    path = catalog.save(tmp_path / "toy.json")

    loaded = store.load("toy", path)
    assert loaded.known() == {"tempura"}
    assert loaded.parked() == {"chopsticks"}
    assert loaded.effects_for("tempura") == TEMPURA
    assert loaded.entries["tempura"].description == "two score five"


def test_a_missing_catalog_is_an_empty_one_not_an_error(tmp_path: Path) -> None:
    """A game nobody has described cards for is the starting state of the exit
    criterion, not a failure."""
    catalog = store.load("never-seen", tmp_path / "absent.json")
    assert len(catalog) == 0
    assert catalog.known() == set()


def test_saved_catalogs_are_readable_by_a_person(tmp_path: Path) -> None:
    """It is committed as evidence of what the system learned, so it has to be legible."""
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", TEMPURA)
    data = json.loads(catalog.save(tmp_path / "toy.json").read_text(encoding="utf-8"))
    assert data["game"] == "toy"
    assert data["cards"]["tempura"]["effects"] == TEMPURA
    assert "learned_at" in data["cards"]["tempura"]


# -- merging --------------------------------------------------------------


def test_merging_never_loses_an_entry() -> None:
    """A catalog is a person's answers accumulated one at a time; dropping any would
    make them answer twice."""
    a = store.Catalog(game="toy")
    a.learn("tempura", TEMPURA)
    b = store.Catalog(game="toy")
    b.learn("maki", MAKI)

    a.merge(b)
    assert a.known() == {"tempura", "maki"}


def test_a_resolved_card_beats_a_parked_one_whichever_side_it_is_on() -> None:
    parked = store.Catalog(game="toy")
    parked.park("maki", "most rolls wins", "unsure")
    resolved = store.Catalog(game="toy")
    resolved.learn("maki", MAKI)

    parked.merge(resolved)
    assert parked.known() == {"maki"}, "learning something must not be undone by a merge"


# -- the catalog outliving the code that wrote it -------------------------


def test_an_entry_that_no_longer_validates_is_reported_not_executed(tmp_path: Path) -> None:
    """Guard the guard. A catalog persists across versions; if the vocabulary changes
    under it, a stale entry must surface rather than be handed to a module."""
    path = tmp_path / "toy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game": "toy",
                "cards": {"relic": {"effects": [{"op": "retired_op"}], "description": "old"}},
            }
        ),
        encoding="utf-8",
    )

    catalog = store.load("toy", path)
    problems = store.audit(catalog)
    assert problems
    assert "relic" in problems[0]


def test_audit_ignores_parked_cards() -> None:
    """A parked card has no effects by definition; reporting it would be noise."""
    catalog = store.Catalog(game="toy")
    catalog.park("chopsticks", "take two", "turn structure")
    assert store.audit(catalog) == []


@pytest.mark.parametrize("effects", [TEMPURA, MAKI, [{"op": "score_tiered", "tiers": [1, 3, 6]}]])
def test_every_sushi_go_shaped_effect_is_expressible(effects: list[dict]) -> None:
    """The vocabulary was designed against real cards; if these failed it would be
    fitted to nothing."""
    assert validate(effects) == []


# -- the vocabulary must not be shaped by one game ------------------------


def test_the_vocabulary_covers_the_categories_plan_md_names() -> None:
    """Guard against fitting the vocabulary to whichever game is in front of me.

    An earlier draft held only scoring operations. That was enough for Sushi Go and
    could not express a single card of the resource game sketched in Phase 1 - no
    gaining, no spending, no forced discard, no conditional flag. plan.md names those
    categories explicitly: "gain resource, force discard, activate on condition, score
    by set".
    """
    cases = {
        "gain a resource": [{"op": "gain", "resource": "fuel", "amount": 2}],
        "spend a resource": [{"op": "spend", "resource": "fuel", "amount": 3}],
        "force a discard": [{"op": "discard_opponent", "count": 1}],
        "draw cards": [{"op": "draw", "count": 2}],
        "set a condition": [{"op": "set_flag", "flag": "ward"}],
        "score by set": [{"op": "score_per_set", "size": 2, "points": 5}],
        "score per resource": [{"op": "score_per_resource", "resource": "fuel",
                                "per": 2, "points": 1}],
    }
    for label, effects in cases.items():
        assert validate(effects) == [], f"cannot express: {label}"


def test_no_single_game_uses_the_whole_vocabulary() -> None:
    """If one game exercised every operation, the vocabulary would be that game's."""
    sushi_go_ops = {"score_flat", "score_per_set", "score_tiered", "score_majority",
                    "score_at_end", "multiply_next"}
    assert sushi_go_ops < set(VOCABULARY), "the vocabulary is wider than one game"


def test_every_operation_field_exists_on_the_contract() -> None:
    """The one real coupling in the design, asserted rather than trusted.

    `EffectOp` is a flat model because a provider needs one concrete schema. Adding an
    operation with a new parameter means adding a field, and forgetting would leave a
    model unable to express something the vocabulary claims to offer.
    """
    from automa.contracts import EffectOp

    available = set(EffectOp.model_fields)
    for op in VOCABULARY.values():
        for one in (*op.required, *op.optional):
            assert one in available, f"{op.name} needs field {one!r} on EffectOp"


# -- a game implements only part of the vocabulary ------------------------


def test_an_operation_this_game_cannot_act_on_is_refused() -> None:
    """Storing it would be the worst outcome available: the card looks catalogued and
    quietly does nothing."""
    sushi_go = {"score_per_set", "score_majority"}
    problems = unsupported([{"op": "gain", "resource": "fuel", "amount": 1}], sushi_go)
    assert problems
    assert "is all this game implements" in problems[0]


def test_a_supported_operation_passes() -> None:
    assert unsupported(TEMPURA, {"score_per_set"}) == []


def test_a_game_that_does_not_declare_is_not_restricted() -> None:
    """Declaring is optional; silently rejecting everything would be worse."""
    assert unsupported([{"op": "gain", "resource": "x", "amount": 1}], None) == []


def test_learning_refuses_an_operation_the_game_will_ignore() -> None:
    catalog = store.Catalog(game="sushigo")
    problems = catalog.learn(
        "wasabi", [{"op": "gain", "resource": "fuel", "amount": 1}],
        supported={"score_per_set", "multiply_next"},
    )
    assert problems
    assert "wasabi" not in catalog


def test_the_prompt_offers_only_what_the_game_can_act_on() -> None:
    """Showing a model an unusable primitive invites a plausible answer that would
    silently do nothing."""
    text = describe_vocabulary({"score_per_set", "multiply_next"})
    assert "score_per_set" in text and "multiply_next" in text
    assert "discard_opponent" not in text
    assert "gain(" not in text



def test_forgetting_a_card_makes_it_askable_again() -> None:
    """A recorded answer is permanent by design, so a wrong one needs a way out.

    Sushi Go's chopsticks was parked as inexpressible when `set_flag` could hold it
    perfectly well, and the module's own chopsticks rule is gated on that flag - so the
    mechanic was switched off by a catalog entry, permanently, because parked cards are
    never asked about again.
    """
    catalog = store.Catalog(game="toy")
    catalog.park("chopsticks", "take two cards", "changes turn structure")
    assert "chopsticks" in catalog.described()

    assert catalog.forget("chopsticks") is True
    assert "chopsticks" not in catalog.described()
    assert "chopsticks" not in catalog
    assert catalog.forget("chopsticks") is False, "forgetting twice is not an error"


def test_forgetting_leaves_every_other_card_alone() -> None:
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", effects=[{"op": "score_flat", "points": 2}], description="two")
    catalog.park("chopsticks", "take two", "turn structure")

    catalog.forget("chopsticks")
    assert catalog.known() == {"tempura"}
