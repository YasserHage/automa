"""Nothing may pre-fill a game's catalog except the product filling it.

Phase 5's exit criterion is a count: a match starting from an empty catalog asks a
question per card type, and a second match asks fewer. The number only means anything if
the catalog really was empty, and `store.load(game)` reads `catalog/<game>.json` with no
path argument - so a file sitting there, from any source, silently answers questions
nobody was asked and drives the headline metric to zero.

A hand-written `catalog/sushigo.json` did sit there. It was written to exercise the
compiled module with real effects, which was worth doing - it is how the `count_field`
disagreement was found - but the product's read path is the wrong place for it, because
the product is what has to produce that file. It now lives in `tests/fixtures/` under a
name that cannot be mistaken for a catalog, and this test is what keeps the read path
clear.

The distinction the whole phase rests on: a catalog is *earned*, one card at a time, from
a person at the table. One that arrives any other way is a claim about the system rather
than evidence about it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from automa.catalog import store

FIXTURE = Path("tests/fixtures/sushigo_reference_effects.json")


def test_no_catalog_is_committed_where_the_product_reads():
    """`catalog/` is the product's output directory, not an input to it.

    A real run writes files here and they belong here - what must never happen is one
    being *committed*, because then it ships with the repository and a fresh clone
    starts with questions already answered. Checked against git rather than the
    filesystem for exactly that reason: the earlier version of this test asserted the
    directory was empty and started failing the moment the product did its job.
    """
    tracked = subprocess.run(
        ["git", "ls-files", str(store.CATALOG_DIR)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    stray = sorted(f for f in tracked if f.endswith(".json"))
    assert not stray, (
        f"{stray} is committed under {store.CATALOG_DIR}/. A shipped catalog answers "
        "questions the product never asked, and makes the phase's headline number - "
        "how many descriptions a first match needs - meaningless."
    )


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
