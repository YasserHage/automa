"""The catalog editor's service: learn, park, forget, and the vocabulary it renders from.

Every write here goes to a `tmp_path`, never to `catalog/`. The product's own
directory stays empty until a real run fills it - `tests/test_catalog_is_earned.py`
enforces that, and seeding a demo catalog "so the editor isn't empty" would destroy
the Phase 5 measurement, which is a count of questions asked starting from nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from automa.catalog import store
from automa.catalog.vocabulary import NAMED_FIELDS, VOCABULARY
from automa.contracts import EffectOp
from automa.core.loader import load_game
from automa.web.services import catalog as service


@pytest.fixture
def sandboxed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the service's path resolution at `tmp_path` for the whole test."""
    monkeypatch.setattr(service, "catalog_path", lambda game: tmp_path / f"{game}.json")
    return tmp_path


def test_the_vocabulary_is_served_as_data_not_a_hardcoded_form() -> None:
    """Twelve hardcoded forms would drift the moment `vocabulary.py` gains a
    thirteenth operation - and that file is explicitly where a person adds one."""
    payload = service.vocabulary()
    assert set(payload["operations"]) == set(VOCABULARY)
    assert payload["named_fields"] == list(NAMED_FIELDS)
    for name, op in payload["operations"].items():
        assert op["required"] == list(VOCABULARY[name].required)
        assert op["optional"] == list(VOCABULARY[name].optional)
        assert op["summary"]


def test_an_effect_op_round_trips_into_something_validate_accepts(sandboxed: Path) -> None:
    """Regression for the `exclude_none` bug.

    `EffectOp` carries all thirteen possible fields, defaulting to `None`, and
    `vocabulary.validate` rejects any field outside the specific operation's own set.
    Without `exclude_none=True` the very first save produces a wall of spurious
    "has no field" errors.
    """
    op = EffectOp(op="score_flat", points=3)
    dumped = op.model_dump(exclude_none=True)
    assert dumped == {"op": "score_flat", "points": 3}

    problems = service.learn("nim", load_game("nim"), "tempura", [dumped])
    assert problems == []

    naive = op.model_dump()  # what the bug looked like
    from automa.catalog.vocabulary import validate

    assert validate([naive]), "without exclude_none it does not validate - hence the fix"


def test_learn_refuses_atomically_and_says_why(sandboxed: Path) -> None:
    """`learn()` validates and refuses without writing; the problem strings are
    written to be read by a person, so they go back verbatim."""
    problems = service.learn("nim", load_game("nim"), "bad", [{"op": "score_flat", "size": 2}])
    assert problems
    assert any("has no field" in p for p in problems)
    assert not (sandboxed / "nim.json").exists(), "nothing was written"


def test_learn_park_forget_round_trip(sandboxed: Path) -> None:
    game = load_game("nim")
    assert service.learn("nim", game, "tempura", [{"op": "score_flat", "points": 5}]) == []

    catalog = service.get_catalog("nim", game)
    assert [c["card_type"] for c in catalog["known"]] == ["tempura"]
    assert catalog["parked"] == []

    service.park("nim", "chopsticks", "take two next turn", "the vocabulary cannot say")
    catalog = service.get_catalog("nim", game)
    assert [c["card_type"] for c in catalog["parked"]] == ["chopsticks"]
    assert catalog["parked"][0]["unresolved"] == "the vocabulary cannot say"

    assert service.forget("nim", "chopsticks") is True
    assert service.forget("nim", "chopsticks") is False, "already gone"
    assert service.get_catalog("nim", game)["parked"] == []


def test_forget_is_the_only_way_to_dislodge_a_wrong_permanent_answer(
    sandboxed: Path,
) -> None:
    """A recorded answer is deliberately permanent - a parked card is never asked
    about again, which is the point - so a *wrong* one is permanent too. This is what
    fixed the chopsticks mis-park."""
    service.park("nim", "chopsticks", "", "wrongly parked")
    assert "chopsticks" in store.load("nim", sandboxed / "nim.json").parked()
    service.forget("nim", "chopsticks")
    assert "chopsticks" not in store.load("nim", sandboxed / "nim.json").entries


def test_terms_declared_follows_what_each_module_actually_declares(sandboxed: Path) -> None:
    """`terms_declared` is what the editor hides its unchecked-value warning on.

    This began life as a tripwire asserting that *no* installed game declared `TERMS`,
    which was true when the warning was written and stopped being true the moment a
    compiled Sushi Go module declared them - so it failed on a correct system, which is
    the wrong shape for a test. The durable claim is per-game rather than global: the
    flag the UI reads must agree with what the module itself declares, so the warning
    appears for exactly the games whose names nothing can check.
    """
    from automa.web.services.games import loadable_games

    for name in loadable_games():
        game = load_game(name)
        declared = service.get_catalog(name, game)["terms_declared"]
        assert declared == (getattr(game, "TERMS", None) is not None), (
            f"{name}: get_catalog reports terms_declared={declared} but the module "
            f"{'declares' if hasattr(game, 'TERMS') else 'does not declare'} TERMS"
        )


def test_a_game_declaring_terms_is_told_which_names_are_allowed(sandboxed: Path) -> None:
    """The other half of the hint: a declared game names its terms instead of warning.

    Sushi Go is the first module to declare `TERMS`, which is what makes the
    `count_field`/`flag`/`resource`/`category` mismatch catchable at all - the class of
    bug that silently switched off the whole chopsticks mechanic (V4 in the README).
    """
    game = load_game("sushigo")
    catalog = service.get_catalog("sushigo", game)
    hint = catalog["terms_hint"]
    assert catalog["terms_declared"] is True
    # Named per field, not per game: every field the module declares is pinned to its
    # allowed values, and any field it leaves out still falls back to the naming
    # convention. Sushi Go declares flag/count_field/category and omits `resource`,
    # so both halves of that appear here at once.
    assert "chopsticks" in hint
    assert "maki" in hint and "pudding" in hint
    assert "nigiri" in hint
    assert "`resource`" in hint and "has not declared which names it uses" in hint


def test_the_terms_hint_is_offered_when_nothing_is_declared(sandboxed: Path) -> None:
    catalog = service.get_catalog("nim", load_game("nim"))
    assert "has not declared which names it uses" in catalog["terms_hint"]


def test_the_audit_banner_reports_entries_that_no_longer_validate(sandboxed: Path) -> None:
    """A catalog outlives the code that wrote it. This has no CLI surface at all."""
    path = sandboxed / "nim.json"
    path.write_text(
        '{"schema_version": 1, "game": "nim", "cards": '
        '{"stale": {"effects": [{"op": "no_such_op"}], "description": "", "learned_at": ""}}}',
        encoding="utf-8",
    )
    catalog = service.get_catalog("nim", load_game("nim"))
    assert catalog["audit"], "a stale entry is reported rather than executed"
    assert "stale" in catalog["audit"][0]


def test_a_write_is_refused_while_a_match_of_that_game_is_running(sandboxed: Path) -> None:
    """The honest guard.

    A live session's `catalog_informant(save=True)` rewrites the whole file on every
    learned card, so an editor change made mid-match could be silently overwritten.
    Refusing is better than losing the person's edit without telling them.
    """
    game = load_game("nim")
    for call in (
        lambda: service.learn("nim", game, "x", [{"op": "score_flat", "points": 1}], busy=True),
        lambda: service.park("nim", "x", "", "r", busy=True),
        lambda: service.forget("nim", "x", busy=True),
    ):
        with pytest.raises(service.CatalogBusyError):
            call()


def test_every_write_reloads_from_disk_first(sandboxed: Path) -> None:
    """Never cache a `Catalog` between requests - a running match writes the same file."""
    game = load_game("nim")
    service.learn("nim", game, "a", [{"op": "score_flat", "points": 1}])

    # Something else (a live match's informant) writes the file behind our back.
    external = store.load("nim", sandboxed / "nim.json")
    external.learn("b", [{"op": "score_flat", "points": 2}])
    external.save()

    service.learn("nim", game, "c", [{"op": "score_flat", "points": 3}])
    final = store.load("nim", sandboxed / "nim.json")
    assert final.known() == {"a", "b", "c"}, "the external write survived"
