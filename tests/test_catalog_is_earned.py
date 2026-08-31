"""A game's catalog is earned, one card at a time, unless the product fills it itself.

Phase 5's exit criterion is a count: a match starting from an empty catalog asks a
question per card type, and a second match asks fewer. `store.load(game)` reads
`catalog/<game>.json` with no path argument, and returns an empty catalog for a game
nobody has described anything for - that is the starting state the exit criterion
measures from, and this file is what keeps that behaviour pinned.

For the submitted repository, `catalog/` is committed deliberately: it is evidence of
what the system actually produced, not a hand-authored stand-in. The distinction this
file still protects is a different one - a *hand-written* catalog is not the same thing
as an *earned* one, even once both are tracked by git. `tests/fixtures/` holds the
hand-written reference effects used to exercise the module before any real match filled
`catalog/`, under a name that cannot be mistaken for the real thing.
"""

from __future__ import annotations

import json
from pathlib import Path

from automa.catalog import store

FIXTURE = Path("tests/fixtures/sushigo_reference_effects.json")


def test_loading_a_game_nobody_has_described_gives_an_empty_catalog():
    catalog = store.load("no-such-game-has-ever-been-played")
    assert len(catalog) == 0
    assert catalog.known() == set()
    assert catalog.described() == set()


def test_the_reference_effects_are_a_fixture_and_not_a_catalog():
    """It may be read by a test on purpose; it may never be found by accident."""
    assert FIXTURE.exists(), "the reference effects went missing"
    assert not str(FIXTURE).startswith(str(store.CATALOG_DIR))

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "cards" in data, "kept in the store's own schema so it can be loaded on purpose"
    assert data["game"] == "sushigo"


def test_the_fixture_still_loads_through_the_store():
    """Its one legitimate use: replaying a compiled module with effects, deliberately."""
    catalog = store.load("sushigo", FIXTURE)
    assert len(catalog) == 12
    assert catalog.parked() == {"chopsticks"}
    assert store.audit(catalog) == []
