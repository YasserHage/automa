"""Starting a match, streaming it, and feeding its two queues.

The SSE handler is the only piece of real asyncio here. It never calls a blocking
`queue.Queue.get()` inside the async generator - it awaits an `asyncio.Queue` that the
worker thread fills through `loop.call_soon_threadsafe`. See `web/streaming.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from automa.core.loader import GameLoadError
from automa.web.models import AnswerRequest, MoveRequest, SessionCreateRequest
from automa.web.routes.deps import sessions
from automa.web.services.session import (
    SessionNotFoundError,
    SessionRegistry,
    TooManySessionsError,
)
from automa.web.streaming import is_close

router = APIRouter(prefix="/api/sessions", tags=["play"])

#: How long an idle SSE connection waits before sending a comment frame. Without it a
#: proxy or a browser can drop a stream that says nothing for minutes, which happens
#: routinely while a person is deciding a move.
_KEEPALIVE_S = 15.0


def _get(registry: SessionRegistry, session_id: str):
    try:
        session = registry.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no session {session_id!r}") from exc
    registry.touch(session_id)
    return session


@router.post("")
def create_session(
    body: SessionCreateRequest, registry: SessionRegistry = Depends(sessions)
) -> dict[str, Any]:
    try:
        session = registry.create(
            game_name=body.game,
            mode=body.mode,
            simulate_deal=body.simulate_deal,
            use_catalog=body.use_catalog,
            seed=body.seed,
            delay=body.delay,
            max_plies=body.max_plies,
        )
    except GameLoadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TooManySessionsError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return session.snapshot()


@router.get("/{session_id}")
def get_session(session_id: str, registry: SessionRegistry = Depends(sessions)) -> dict[str, Any]:
    return _get(registry, session_id).snapshot()


@router.get("/{session_id}/events")
async def session_events(
    session_id: str,
    request: Request,
    registry: SessionRegistry = Depends(sessions),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    session = _get(registry, session_id)
    try:
        after = int(last_event_id) if last_event_id else 0
    except ValueError:
        after = 0

    async def stream() -> AsyncIterator[str]:
        async with session.broadcaster.subscribe(after=after) as events:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(events.get(), timeout=_KEEPALIVE_S)
                except TimeoutError:
                    # A browser disconnect is not an abandon - SSE reconnects are
                    # routine - so this only keeps the connection warm.
                    registry.touch(session_id)
                    yield ": keepalive\n\n"
                    continue
                if is_close(item):
                    return
                yield item.encode()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/move")
def make_move(
    session_id: str, body: MoveRequest, registry: SessionRegistry = Depends(sessions)
) -> dict[str, Any]:
    """Feed the decision queue.

    Validated against what the session is actually waiting for, so an out-of-range
    index is an instant 400 rather than a round-trip through SSE. The decider
    re-checks anyway and re-prompts on a bad one - that loop is load-bearing, because
    an out-of-range index reaching `play_match` raises `IllegalDecision`, which is not
    caught by its `except MatchAbandoned` and would kill the match with no
    `match_end`.
    """
    session = _get(registry, session_id)
    if session.status != "running":
        raise HTTPException(status_code=409, detail=f"session is {session.status}")
    pending = session.pending
    if not pending or pending.get("kind") != "turn":
        raise HTTPException(status_code=409, detail="it is not your turn")
    options = pending.get("options") or []
    if not 0 <= body.index < len(options):
        raise HTTPException(
            status_code=400,
            detail=f"{body.index} is not one of 0-{len(options) - 1}",
        )
    session.decisions.put(body.index)
    return {"accepted": True, "chose": options[body.index]}


@router.post("/{session_id}/answer")
def answer_question(
    session_id: str, body: AnswerRequest, registry: SessionRegistry = Depends(sessions)
) -> dict[str, Any]:
    """Feed the answer queue. An empty body is a real answer: "I cannot say"."""
    session = _get(registry, session_id)
    if session.status != "running":
        raise HTTPException(status_code=409, detail=f"session is {session.status}")
    pending = session.pending
    if not pending or pending.get("kind") != "question":
        raise HTTPException(status_code=409, detail="no question is waiting")
    session.answers.put(body.text or "")
    return {"accepted": True}


@router.delete("/{session_id}")
def abandon_session(
    session_id: str, registry: SessionRegistry = Depends(sessions)
) -> dict[str, Any]:
    session = _get(registry, session_id)
    session.abandon("quit")
    return {"abandoned": True}
