"""The shape of one server-sent event, and how it is encoded on the wire.

Every session (a match, a compile job) keeps one append-only, ``seq``-numbered log of
these. ``EventSource`` handles reconnect and replay from ``Last-Event-ID`` on its own,
which is the whole reason SSE needs nothing beyond this plus a per-connection cursor
into the log - see :mod:`automa.web.streaming`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    """One event, numbered in the order it was published."""

    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> str:
        """As an SSE wire frame: an id, an event name, and one JSON data line."""
        payload = json.dumps({"seq": self.seq, "type": self.type, **self.data}, default=str)
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"
