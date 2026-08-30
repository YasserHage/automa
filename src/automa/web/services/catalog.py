"""Read, learn, park, forget and describe catalog entries - the catalog editor's service.

Every write reloads the catalog from disk first and holds a per-game lock for the
duration (CLAUDE.md's concurrency rule): never cache a ``Catalog`` between requests,
because a live match's ``catalog_informant(save=True)`` rewrites the whole file on
every learned card, and an editor built on a stale in-memory copy would silently undo
that.
"""

from __future__ import annotations

import threading
from types import ModuleType
from typing import Any

from automa.catalog import store
from automa.catalog.store import terms_for
from automa.catalog.vocabulary import NAMED_FIELDS, VOCABULARY, describe_terms
from automa.web.paths import card_type as validate_card_type
from automa.web.paths import catalog_path


class CatalogBusyError(RuntimeError):
    """A live match session for this game is running; the catalog is read-only."""


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(game: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(game)
        if lock is None:
            lock = threading.Lock()
            _locks[game] = lock
        return lock


def vocabulary() -> dict[str, Any]:
    """`VOCABULARY` as JSON - the form the client builds its editor forms from."""
    return {
        "operations": {
            name: {
                "required": list(op.required),
                "optional": list(op.optional),
                "summary": op.summary,
            }
            for name, op in sorted(VOCABULARY.items())
        },
        "named_fields": list(NAMED_FIELDS),
    }


def _entry_out(card: str, entry: Any) -> dict[str, Any]:
    return {
        "card_type": card,
        "effects": entry.effects,
        "description": entry.description,
        "unresolved": entry.unresolved,
        "learned_at": entry.learned_at,
        "playable": entry.playable,
    }


def get_catalog(game: str, game_module: ModuleType | None = None) -> dict[str, Any]:
    catalog = store.load(game, catalog_path(game))
    terms = terms_for(game_module) if game_module is not None else None
    return {
        "game": game,
        "known": [_entry_out(c, catalog.entries[c]) for c in sorted(catalog.known())],
        "parked": [_entry_out(c, catalog.entries[c]) for c in sorted(catalog.parked())],
        "audit": store.audit(catalog),
        "terms_declared": terms is not None,
        "terms_hint": describe_terms(terms),
    }


def learn(
    game: str,
    game_module: ModuleType,
    ctype: str,
    effects: list[dict[str, Any]],
    description: str = "",
    *,
    busy: bool = False,
) -> list[str]:
    """Validate and store. Returns problem strings verbatim; empty means accepted."""
    if busy:
        raise CatalogBusyError(f"a running session of {game!r} holds the catalog")
    ctype = validate_card_type(ctype)
    supported = getattr(game_module, "SUPPORTED_OPS", None)
    supported = set(supported) if supported is not None else None
    terms = terms_for(game_module)
    with _lock_for(game):
        catalog = store.load(game, catalog_path(game))
        problems = catalog.learn(ctype, effects, description, supported=supported, terms=terms)
        if not problems:
            catalog.save()
        return problems


def park(game: str, ctype: str, description: str, reason: str, *, busy: bool = False) -> None:
    if busy:
        raise CatalogBusyError(f"a running session of {game!r} holds the catalog")
    ctype = validate_card_type(ctype)
    with _lock_for(game):
        catalog = store.load(game, catalog_path(game))
        catalog.park(ctype, description, reason)
        catalog.save()


def forget(game: str, ctype: str, *, busy: bool = False) -> bool:
    if busy:
        raise CatalogBusyError(f"a running session of {game!r} holds the catalog")
    ctype = validate_card_type(ctype)
    with _lock_for(game):
        catalog = store.load(game, catalog_path(game))
        removed = catalog.forget(ctype)
        if removed:
            catalog.save()
        return removed


def describe(
    game: str,
    game_module: ModuleType,
    ctype: str,
    description: str,
    *,
    busy: bool = False,
) -> dict[str, Any]:
    """Structure free text into vocabulary operations. One LLM call."""
    from automa.agents.cataloguer import catalogue_card
    from automa.config import describe as describe_config
    from automa.trajectory import TrajectoryWriter

    if busy:
        raise CatalogBusyError(f"a running session of {game!r} holds the catalog")
    ctype = validate_card_type(ctype)
    supported = getattr(game_module, "SUPPORTED_OPS", None)
    supported = set(supported) if supported is not None else None
    terms = terms_for(game_module)

    with TrajectoryWriter(f"web-describe-{game}", config=describe_config()) as traj:
        card = catalogue_card(
            ctype, description, game=game, supported=supported, terms=terms, trajectory=traj
        )
        calls, cost = traj.llm_calls, traj.total_cost_usd

    if not card.ok:
        return {
            "ok": False,
            "unresolved": card.unresolved,
            "llm_calls": calls,
            "cost_usd": cost,
        }

    with _lock_for(game):
        catalog = store.load(game, catalog_path(game))
        problems = catalog.learn(
            ctype, card.effects, card.description, supported=supported, terms=terms
        )
        if not problems:
            catalog.save()
    return {
        "ok": not problems,
        "effects": card.effects,
        "problems": problems,
        "llm_calls": calls,
        "cost_usd": cost,
    }
