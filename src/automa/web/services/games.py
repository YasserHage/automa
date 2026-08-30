"""List installed games and report on one, without ever raising the listing away.

Thin over :mod:`automa.core.loader` and :mod:`automa.catalog.store`, and pure: every
function here returns a plain dict or raises one of the errors below, never touches
HTTP, and can be exercised from a test with no server running.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

from automa.catalog import store
from automa.core.game import conforms, settle
from automa.core.loader import GAMES_PACKAGE, GameLoadError, available_games, load_game
from automa.web.paths import catalog_path


class GameBusyError(RuntimeError):
    """A live match session holds this game's module; it cannot be reloaded now."""


def loadable_games() -> list[str]:
    """Every game name the loader can actually import.

    ``available_games()`` enumerates every top-level module under ``automa.games``,
    including one left behind mid-rename by another process - ``sushigo-old``, at the
    time this was written. A name that is not a valid Python identifier cannot be
    imported as one, so it is filtered here rather than offered as a playable game and
    failing the moment somebody clicks it.
    """
    return [name for name in available_games() if name.isidentifier()]


def _import(name: str) -> ModuleType:
    return importlib.import_module(f"{GAMES_PACKAGE}.{name}")


def _catalog_counts(name: str) -> dict[str, int]:
    catalog = store.load(name, catalog_path(name))
    return {"known": len(catalog.known()), "parked": len(catalog.parked())}


def list_games() -> list[dict[str, Any]]:
    """One summary per loadable game. A non-conforming module is reported, not raised.

    ``name`` is the **module** name, because that is what `load_game` resolves and
    therefore the only addressable identity. A module's own ``NAME`` is reported
    separately as ``declared_name``: two installed modules can legitimately declare
    the same one - `dnup.py` and `dnup_compiled.py` both say ``NAME = "dnup"`` - and
    keying the listing on that made one of them unreachable and mislabelled the other.
    The catalog still follows ``NAME``, since `catalog/<NAME>.json` is where the
    module's own effects live.
    """
    summaries: list[dict[str, Any]] = []
    for name in loadable_games():
        try:
            module = _import(name)
            problems = conforms(module)
        except Exception as exc:  # a broken module must not take the whole listing down
            summaries.append(
                {
                    "name": name,
                    "declared_name": None,
                    "players": None,
                    "conforms": False,
                    "problems": [f"{type(exc).__name__}: {exc}"],
                    "catalog": {"known": 0, "parked": 0},
                }
            )
            continue
        declared = getattr(module, "NAME", name)
        summaries.append(
            {
                "name": name,
                "declared_name": declared,
                "players": list(getattr(module, "PLAYERS", ()) or ()),
                "conforms": not problems,
                "problems": problems,
                "catalog": _catalog_counts(declared) if declared.isidentifier() else
                {"known": 0, "parked": 0},
            }
        )
    return summaries


def game_detail(name: str) -> dict[str, Any]:
    """A loadable game's full detail, or raise if it does not conform at all."""
    module = load_game(name)
    terms = getattr(module, "TERMS", None)
    supported = getattr(module, "SUPPORTED_OPS", None)
    # A sample of the state shape only - for display, never fed back into a real
    # match. `settle()` is safe here because this route has no informant of its own
    # and is not the match session, which must never call it.
    state = settle(module, module.initial_state(seed=0))
    return {
        "name": name,
        "declared_name": module.NAME,
        "players": list(module.PLAYERS),
        "conforms": True,
        "problems": [],
        "supported_ops": sorted(supported) if supported is not None else None,
        "terms_declared": terms is not None,
        "state_keys": sorted(state.keys()) if isinstance(state, dict) else [],
        "catalog": _catalog_counts(module.NAME),
    }


def validate_game(name: str, matches: int = 100) -> dict[str, Any]:
    """Random self-play against an installed module. No LLM call anywhere in this path."""
    from automa.core.selfplay import validate_module

    module = load_game(name)
    report = validate_module(module, matches=matches)
    return report.to_dict()


def reload_game(name: str, *, busy: bool) -> dict[str, Any]:
    """Re-import an installed module (after an install) and re-check it conforms.

    Refused with :class:`GameBusyError` while a live session holds the same module
    object - `importlib.reload` mutates it in place, which would pull the rug out
    from under `MatchSession.game` mid-match.
    """
    if busy:
        raise GameBusyError(f"a running session holds {name!r}; finish or abandon it first")

    importlib.invalidate_caches()
    module_name = f"{GAMES_PACKAGE}.{name}"
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    try:
        module = load_game(name)
    except GameLoadError:
        raise
    return {"name": module.NAME, "players": list(module.PLAYERS), "conforms": True}
