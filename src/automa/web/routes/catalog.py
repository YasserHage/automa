"""The catalog editor's HTTP surface.

The one place `automa.contracts` is legitimately reused: `EffectOp` types the effect
payload. Note the `exclude_none=True` on the way through - `EffectOp` carries every
field any operation can take, all defaulting to `None`, and `vocabulary.validate`
rejects any field outside the specific operation's own set. Without it the very first
save produces a wall of spurious "has no field" errors.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from automa.core.loader import GameLoadError, load_game
from automa.web.models import (
    CatalogResponse,
    DescribeCardRequest,
    DescribeCardResponse,
    LearnCardRequest,
    LearnCardResponse,
    ParkCardRequest,
    VocabularyResponse,
)
from automa.web.paths import InvalidNameError
from automa.web.paths import game_name as validate_game_name
from automa.web.routes.deps import sessions
from automa.web.services import catalog as service
from automa.web.services.session import SessionRegistry

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/vocabulary", response_model=VocabularyResponse)
def vocabulary() -> dict[str, Any]:
    """The closed operation set, as data.

    The editor's forms are generated from this rather than hardcoded, so a thirteenth
    operation added to `vocabulary.py` appears in the UI without anyone editing it.
    """
    return service.vocabulary()


def _game(game: str):
    try:
        return load_game(validate_game_name(game))
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GameLoadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _busy(registry: SessionRegistry, declared_name: str) -> bool:
    """Whether a live match is writing this game's catalog file - keyed on `NAME`,
    which is what `catalog/<NAME>.json` follows, not on the module name."""
    return registry.is_catalog_busy(declared_name)


@router.get("/games/{game}/catalog", response_model=CatalogResponse)
def get_catalog(game: str) -> dict[str, Any]:
    module = _game(game)
    return service.get_catalog(module.NAME, module)


@router.put("/games/{game}/catalog/cards/{ctype}", response_model=LearnCardResponse)
def learn_card(
    game: str,
    ctype: str,
    body: LearnCardRequest,
    registry: SessionRegistry = Depends(sessions),
) -> dict[str, Any]:
    module = _game(game)
    effects = [e.model_dump(exclude_none=True) for e in body.effects]
    try:
        problems = service.learn(
            module.NAME,
            module,
            ctype,
            effects,
            body.description,
            busy=_busy(registry, module.NAME),
        )
    except service.CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if problems:
        # 422 with the store's own problem strings, verbatim - they are written to be
        # read by a person, and rewording them would lose the detail that makes them
        # actionable.
        raise HTTPException(status_code=422, detail={"ok": False, "problems": problems})
    return {"ok": True, "problems": []}


@router.post("/games/{game}/catalog/cards/{ctype}/park", response_model=LearnCardResponse)
def park_card(
    game: str,
    ctype: str,
    body: ParkCardRequest,
    registry: SessionRegistry = Depends(sessions),
) -> dict[str, Any]:
    module = _game(game)
    try:
        service.park(
            module.NAME, ctype, body.description, body.reason, busy=_busy(registry, module.NAME)
        )
    except service.CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "problems": []}


@router.delete("/games/{game}/catalog/cards/{ctype}", response_model=LearnCardResponse)
def forget_card(
    game: str, ctype: str, registry: SessionRegistry = Depends(sessions)
) -> dict[str, Any]:
    module = _game(game)
    try:
        removed = service.forget(module.NAME, ctype, busy=_busy(registry, module.NAME))
    except service.CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"nothing recorded for {ctype!r}")
    return {"ok": True, "problems": []}


@router.post(
    "/games/{game}/catalog/cards/{ctype}/describe", response_model=DescribeCardResponse
)
def describe_card(
    game: str,
    ctype: str,
    body: DescribeCardRequest,
    registry: SessionRegistry = Depends(sessions),
) -> dict[str, Any]:
    """Free text through the Cataloguer into vocabulary operations. One LLM call."""
    module = _game(game)
    try:
        return service.describe(
            module.NAME, module, ctype, body.description, busy=_busy(registry, module.NAME)
        )
    except service.CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
