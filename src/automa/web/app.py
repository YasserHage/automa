"""The FastAPI application: routes, static files, and the two registries.

`automa/web/` sits outside `automa/core/`, and `tests/_ast_imports.py` walks only
`src/automa/core`, so this package may freely import games, the LLM layer and the
compiler without touching invariants 1, 2 or 4. The core itself is unchanged - the web
is an adapter over functions that already existed.

**No module-level mutable state.** `create_app()` constructs a `SessionRegistry` and a
`JobRegistry` per application and hangs them on `app.state`; routes reach them through
a dependency. That is the direct antidote to `scripts/play.py`'s `_LEARNED` and
`_UNANSWERED` globals, which the web never imports.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from automa.web.routes import catalog, compiling, games, meta, play
from automa.web.services.compiling import JobRegistry
from automa.web.services.session import SessionRegistry

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: A JSON body larger than this is refused before it is parsed. Nothing the API
#: accepts is remotely this big; uploads go through multipart, which has its own cap.
MAX_JSON_BYTES = 1 * 1024 * 1024


def create_app(*, start_janitor: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.sessions = SessionRegistry(start_janitor=start_janitor)
        application.state.jobs = JobRegistry()
        try:
            yield
        finally:
            # Drains rather than kills: pushing the abandon sentinel lets `play_match`
            # unwind through `MatchAbandoned` and `TrajectoryWriter.__exit__` write
            # `run_end`. Skipping it leaves a truncated trajectory, which is a direct
            # hit on invariant 3's audit value.
            application.state.sessions.shutdown()
            application.state.jobs.shutdown()

    app = FastAPI(
        title="Automa",
        description="A local web surface over the Automa CLI. The CLI stays canonical.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        content_type = request.headers.get("content-type", "")
        if (
            length
            and length.isdigit()
            and int(length) > MAX_JSON_BYTES
            and not content_type.startswith("multipart/")
        ):
            return JSONResponse(
                status_code=413, content={"detail": "request body is too large"}
            )
        return await call_next(request)

    for module in (meta, games, catalog, play, compiling):
        app.include_router(module.router)

    # Mounted last, at the root, so every /api route above wins. `html=True` serves
    # index.html for `/`; the client hash-routes from there, so no server-side
    # rewrite rule is ever needed.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def openapi_json() -> str:
    """The API description, for anyone who wants to read it without starting a server."""
    return json.dumps(create_app(start_janitor=False).openapi(), indent=2)
