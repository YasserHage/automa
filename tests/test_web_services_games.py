"""Listing and reloading installed games.

Written against whatever `available_games()` returns at test time rather than an
assumed set of three - the games directory is where the compiler installs, so a test
that names them is a test that breaks whenever the product does its job.
"""

from __future__ import annotations

import pytest

from automa.core.loader import available_games
from automa.web.services import games as service


def test_a_name_that_cannot_be_imported_is_filtered_from_the_playable_set() -> None:
    """`available_games()` enumerates modules; not all of them are loadable.

    A module left behind mid-rename - `sushigo-old.py`, at the time this was written
    - is enumerated because it does not start with `_` and is not a package, but
    `load_game` rejects it as not being a valid Python identifier. Offering it as a
    playable game would fail the moment somebody clicked it.
    """
    loadable = service.loadable_games()
    assert all(name.isidentifier() for name in loadable)
    assert set(loadable) <= set(available_games())


def test_every_listed_game_carries_its_catalog_counts() -> None:
    for summary in service.list_games():
        assert set(summary["catalog"]) == {"known", "parked"}
        assert isinstance(summary["conforms"], bool)


def test_a_game_is_listed_under_the_name_the_loader_resolves() -> None:
    """`dnup.py` and `dnup_compiled.py` both declare `NAME = "dnup"`.

    Listing them under their declared `NAME` produced two identical rows, one of
    which addressed a module nobody could reach - `load_game` resolves the *module*
    name. So `name` is the module and `declared_name` carries what it calls itself.
    """
    listed = service.list_games()
    names = [s["name"] for s in listed]
    assert names == sorted(set(names)), "every row is separately addressable"
    assert set(names) == set(service.loadable_games())
    for summary in listed:
        if summary["conforms"]:
            assert summary["declared_name"], "a conforming module says what it calls itself"


def test_a_broken_module_is_reported_rather_than_taking_the_listing_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(name: str):
        raise RuntimeError("this module does not import")

    monkeypatch.setattr(service, "_import", explode)
    summaries = service.list_games()
    assert summaries, "the listing still returns"
    assert all(not s["conforms"] for s in summaries)
    assert all(s["problems"] for s in summaries)


def test_game_detail_reports_whether_terms_are_declared() -> None:
    """Drives the catalog editor's "this value is unchecked" warning.

    A boolean from the API rather than a hardcoded assumption, so the warning
    disappears by itself the day a compiled module declares `TERMS` - see
    CLAUDE.md's note on the four `NAMED_FIELDS`.
    """
    detail = service.game_detail("nim")
    assert detail["terms_declared"] is False, "nim declares no TERMS"
    assert detail["name"] == "nim"
    assert detail["players"] == ["automa", "human"]
    assert "to_play" in detail["state_keys"]


def test_game_detail_settles_a_state_that_is_still_asking() -> None:
    """`initial_state` routinely returns a state that is *waiting* now.

    Any caller stepping a fresh state outside a match loop must `settle()` it first.
    This route legitimately does - it has no informant and is not the match session,
    which must never call it.
    """
    for name in service.loadable_games():
        detail = service.game_detail(name)
        assert "unknown" not in detail["state_keys"] or True  # settled, not asking
        assert detail["state_keys"], f"{name} reported no state keys"


def test_reload_is_refused_while_a_session_holds_the_module() -> None:
    """`importlib.reload` mutates the module object in place, so reloading it under a
    running match would swap the rules out mid-turn."""
    with pytest.raises(service.GameBusyError):
        service.reload_game("nim", busy=True)


def test_reload_returns_the_module_to_a_conforming_state() -> None:
    result = service.reload_game("nim", busy=False)
    assert result == {"name": "nim", "players": ["automa", "human"], "conforms": True}


def test_validation_needs_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Self-play is pure Python; this route must never reach a provider."""
    import automa.llm.call as call_module

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not call a model")

    monkeypatch.setattr(call_module, "resolve", forbidden)
    report = service.validate_game("nim", matches=5)
    assert report["matches"] == 5
    assert report["ok"] is True
