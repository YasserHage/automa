"""Listing installed games, reporting on one, validating and reloading.

Thin: every route here is a few lines of HTTP over `web.services.games`, which is
where the behaviour and the tests live.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from automa.core.loader import GameLoadError
from automa.web.models import GameDetail, GameSummary, ReloadResponse
from automa.web.paths import InvalidNameError
from automa.web.paths import game_name as validate_game_name
from automa.web.routes.deps import sessions
from automa.web.services import games as service
from automa.web.services.session import SessionRegistry

router = APIRouter(prefix="/api/games", tags=["games"])


def _checked(game: str) -> str:
    try:
        return validate_game_name(game)
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[GameSummary])
def list_games() -> list[dict[str, Any]]:
    return service.list_games()


@router.get("/{game}", response_model=GameDetail)
def game_detail(game: str) -> dict[str, Any]:
    try:
        return service.game_detail(_checked(game))
    except GameLoadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{game}/validate")
def validate_game(game: str, matches: int = 100) -> dict[str, Any]:
    """Random self-play against an installed module. No LLM call on this path."""
    try:
        return service.validate_game(_checked(game), matches=matches)
    except GameLoadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{game}/reload", response_model=ReloadResponse)
def reload_game(
    game: str, registry: SessionRegistry = Depends(sessions)
) -> dict[str, Any]:
    """Re-import a module after an install.

    Refused with 409 while a live session holds it: `importlib.reload` mutates the
    module object in place, so reloading under a running match would swap the rules
    out from beneath it mid-turn.
    """
    name = _checked(game)
    try:
        return service.reload_game(name, busy=registry.is_module_busy(name))
    except service.GameBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GameLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
