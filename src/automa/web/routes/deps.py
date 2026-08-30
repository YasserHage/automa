"""Reaching the registries a route needs.

`create_app()` constructs exactly one `SessionRegistry` and one `JobRegistry` and hangs
them on `app.state`. Routes get them through these dependencies rather than from a
module-level global, which is what lets a test build its own app with nothing shared
between them - the direct antidote to `scripts/play.py`'s `_LEARNED`/`_UNANSWERED`.
"""

from __future__ import annotations

from fastapi import Request

from automa.web.services.compiling import JobRegistry
from automa.web.services.session import SessionRegistry


def sessions(request: Request) -> SessionRegistry:
    return request.app.state.sessions


def jobs(request: Request) -> JobRegistry:
    return request.app.state.jobs
