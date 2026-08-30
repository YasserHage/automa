"""Drafting a catalog from rule material, on scripted responses.

The property worth most of this file is that **absent is not parked**. A card the
material never describes has to come out of drafting still absent, so the table asks
about it on first sight. A card the material describes but the vocabulary cannot hold is
parked, and never asked about again. Confusing the two in the safe-looking direction -
parking whatever was not found - would quietly make those cards unaskable, and the system
would behave as though it had settled questions nobody ever answered.

Nothing here reaches a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _providers import ScriptedProvider, structured_response

from automa.catalog import store
from automa.catalog.build import CatalogBuildError, draft_catalog
from automa.contracts import CatalogDraft

TEMPURA = {"op": "score_per_set", "size": 2, "points": 5}
NIGIRI = {"op": "score_flat", "points": 2}
SUPPORTED = {"score_per_set", "score_flat", "score_majority"}


@pytest.fixture
def material(tmp_path: Path) -> Path:
    """A text-layer PDF stands in for submitted material; ingestion is tested elsewhere."""
    path = tmp_path / "rules.txt"
    path.write_text("Tempura: two score five. Salmon nigiri: two points.", encoding="utf-8")
    return path


def draft(*cards, not_described=(), notes=""):
    """A scripted CatalogDraft response."""
    return structured_response(
        CatalogDraft(cards=list(cards), not_described=list(not_described), notes=notes)
    )


def card(card_type, effects=(), cannot="", evidence=""):
    return {
        "card_type": card_type,
        "effects": list(effects),
        "cannot_express": cannot,
        "evidence": evidence,
    }


def test_what_the_material_states_is_recorded(install_provider, material):
    install_provider(
        ScriptedProvider(
            draft(
                card("tempura", [TEMPURA], evidence="two score five"),
                card("salmon_nigiri", [NIGIRI], evidence="two points"),
            )
        )
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert catalog.known() == {"tempura", "salmon_nigiri"}
    assert catalog.effects_for("tempura") == [TEMPURA]
    assert catalog.entries["tempura"].description == "two score five"


def test_a_card_the_material_does_not_describe_is_left_absent(install_provider, material):
    """The whole point: it must still be askable at the table."""
    install_provider(
        ScriptedProvider(draft(card("tempura", [TEMPURA]), not_described=["pudding"]))
    )
    catalog, result = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert "pudding" not in catalog
    assert "pudding" not in catalog.described(), "an unmentioned card must stay askable"
    assert catalog.parked() == set()
    assert result.not_described == ["pudding"]


def test_a_card_the_vocabulary_cannot_hold_is_parked(install_provider, material):
    install_provider(
        ScriptedProvider(
            draft(card("chopsticks", cannot="changes how many cards you take, not scoring"))
        )
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert catalog.parked() == {"chopsticks"}
    assert "chopsticks" in catalog.described(), "parked means never asked again"


def test_an_operation_this_game_does_not_implement_is_parked_not_stored(
    install_provider, material
):
    """Storing it would leave a card that looks catalogued and does nothing."""
    install_provider(
        ScriptedProvider(draft(card("coin", [{"op": "gain", "resource": "coin", "amount": 1}])))
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert catalog.known() == set()
    assert catalog.parked() == {"coin"}
    assert "gain" in catalog.entries["coin"].unresolved


def test_a_count_field_the_module_does_not_derive_is_parked(install_provider, material):
    install_provider(
        ScriptedProvider(
            draft(card("maki_2", [{"op": "score_majority", "first": 6, "count_field": "rolls"}]))
        )
    )
    catalog, _ = draft_catalog(
        "toy", material=material, supported=SUPPORTED, terms={"count_field": {"icons"}}, save=False
    )
    assert catalog.parked() == {"maki_2"}


def test_a_card_claimed_as_described_but_carrying_nothing_stays_absent(install_provider, material):
    """No effects and no refusal is not an answer, so nobody has answered it."""
    install_provider(ScriptedProvider(draft(card("mystery"))))
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert "mystery" not in catalog.described()


def test_cards_already_recorded_are_left_alone(install_provider, material):
    """Re-running drafting must not overwrite what a person said at the table."""
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", effects=[NIGIRI], description="what the player told us")

    install_provider(ScriptedProvider(draft(card("tempura", [TEMPURA], evidence="rulebook"))))
    catalog, _ = draft_catalog(
        "toy", material=material, catalog=catalog, supported=SUPPORTED, save=False
    )

    assert catalog.effects_for("tempura") == [NIGIRI]
    assert catalog.entries["tempura"].description == "what the player told us"


def test_what_is_already_known_is_named_in_the_prompt(install_provider, material):
    catalog = store.Catalog(game="toy")
    catalog.learn("tempura", effects=[TEMPURA], description="")

    provider = install_provider(ScriptedProvider(draft(card("salmon_nigiri", [NIGIRI]))))
    draft_catalog("toy", material=material, catalog=catalog, supported=SUPPORTED, save=False)

    sent = str(provider.requests[0].messages)
    assert "tempura" in sent and "Already recorded" in sent


def test_the_deck_list_is_sent_when_supplied(install_provider, material):
    provider = install_provider(ScriptedProvider(draft(card("tempura", [TEMPURA]))))
    draft_catalog(
        "toy",
        material=material,
        card_material="tempura x14, sashimi x14",
        supported=SUPPORTED,
        save=False,
    )
    assert "sashimi x14" in str(provider.requests[0].messages)


def test_an_unreachable_provider_writes_nothing(install_provider, material):
    install_provider(ScriptedProvider(*[TimeoutError("503 unavailable")] * 3))
    with pytest.raises(CatalogBuildError) as exc:
        draft_catalog("toy", material=material, supported=SUPPORTED, save=False)
    assert "asked about at the table" in str(exc.value), "the fallback must be stated"


def test_drafting_nothing_is_not_an_error(install_provider, material):
    """A rulebook that explains the turn order and prints values on the cards."""
    install_provider(ScriptedProvider(draft(not_described=["tempura", "sashimi"])))
    catalog, result = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert len(catalog) == 0
    assert sorted(result.not_described) == ["sashimi", "tempura"]


def test_the_catalog_is_written_where_the_product_reads(
    install_provider, material, tmp_path, monkeypatch
):
    monkeypatch.setattr(store, "CATALOG_DIR", tmp_path)
    install_provider(ScriptedProvider(draft(card("tempura", [TEMPURA]))))

    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=True)
    assert (tmp_path / "toy.json").exists()
    assert store.load("toy", tmp_path / "toy.json").known() == {"tempura"}


# -- a malformed answer is not a verdict about the card -------------------
#
# The first real drafting run returned `score_per_set` with a set size and no points for
# tempura and sashimi. Both were parked, which marks a card described - so the table
# would never have asked, and the two simplest cards in the game would have scored
# nothing for good. These pin the two halves of the fix.


BAD_SET = {"op": "score_per_set", "size": 2}  # no `points`


def test_a_malformed_card_is_re_asked_with_the_problem(install_provider, material):
    provider = install_provider(
        ScriptedProvider(
            draft(card("tempura", [BAD_SET]), card("salmon_nigiri", [NIGIRI])),
            draft(card("tempura", [TEMPURA])),
        )
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert len(provider.requests) == 2, "a missing field must be fed back, not accepted"
    assert "points" in str(provider.requests[1].messages), "the retry must say what was wrong"
    assert catalog.effects_for("tempura") == [TEMPURA]


def test_the_cards_that_were_fine_survive_a_repair_round(install_provider, material):
    """Eleven good entries must not be lost because one came back short of a field."""
    install_provider(
        ScriptedProvider(
            draft(card("tempura", [BAD_SET]), card("salmon_nigiri", [NIGIRI])),
            draft(card("tempura", [TEMPURA])),
        )
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)
    assert catalog.known() == {"tempura", "salmon_nigiri"}


def test_a_card_still_malformed_after_repair_is_left_absent_not_parked(
    install_provider, material
):
    """The model failed; that is not the card telling us it cannot be expressed."""
    install_provider(
        ScriptedProvider(
            draft(card("tempura", [BAD_SET]), card("salmon_nigiri", [NIGIRI])),
            draft(card("tempura", [BAD_SET])),
        )
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert catalog.parked() == set(), "parking would make it permanently unaskable"
    assert "tempura" not in catalog.described(), "the table must still be asked"
    assert catalog.known() == {"salmon_nigiri"}, "the good card is still kept"


def test_a_provider_failing_mid_repair_keeps_what_already_worked(install_provider, material):
    install_provider(
        ScriptedProvider(
            draft(card("tempura", [BAD_SET]), card("salmon_nigiri", [NIGIRI])),
            *[TimeoutError("503 unavailable")] * 3,
        )
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)
    assert catalog.known() == {"salmon_nigiri"}


def test_a_declined_card_is_not_re_asked(install_provider, material):
    """`cannot_express` is a real answer; pressing again would invite a guess."""
    provider = install_provider(
        ScriptedProvider(draft(card("chopsticks", cannot="changes turn structure")))
    )
    catalog, _ = draft_catalog("toy", material=material, supported=SUPPORTED, save=False)

    assert len(provider.requests) == 1
    assert catalog.parked() == {"chopsticks"}
