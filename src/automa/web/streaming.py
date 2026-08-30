"""Thread-safe publish, asyncio-safe subscribe: the fan-out behind every SSE stream.

A session's own worker thread produces events (a turn resolved, a question was asked,
the compiler tried again); an SSE request handler, running on the asyncio event loop,
wants to read them live. :class:`Broadcaster` is the hand-off between the two: the
only method a foreign thread may call is :meth:`publish`, and it never touches a
subscriber's queue directly - it hands the item to *that subscriber's own* event loop
with ``call_soon_threadsafe``, which is the one thing that makes crossing from a
blocking ``queue.Queue.get()`` into an async generator safe.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from automa.web.events import Event

#: Put on a subscriber's queue when the broadcaster closes, so a `queue.get()` awaiting
#: the next event wakes up and the stream ends instead of hanging forever.
_CLOSE = object()


def is_close(item: object) -> bool:
    return item is _CLOSE


class Broadcaster:
    """One append-only, ``seq``-numbered event log with live fan-out to subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: list[Event] = []
        self._next_seq = 1
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._next_sub_id = 0
        self._closed = False

    def publish(self, event_type: str, **data: Any) -> Event:
        with self._lock:
            event = Event(seq=self._next_seq, type=event_type, data=data)
            if self._closed:
                # A late publish racing close() (e.g. a `finally` block) is dropped:
                # nothing is listening to a closed broadcaster's return value.
                return event
            self._next_seq += 1
            self._history.append(event)
            subscribers = list(self._subscribers.values())
        for loop, sub_queue in subscribers:
            loop.call_soon_threadsafe(sub_queue.put_nowait, event)
        return event

    def close(self) -> None:
        """Stop accepting new subscribers to history and wake every live one."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = list(self._subscribers.values())
        for loop, sub_queue in subscribers:
            loop.call_soon_threadsafe(sub_queue.put_nowait, _CLOSE)

    def has_subscribers(self) -> bool:
        with self._lock:
            return bool(self._subscribers)

    @asynccontextmanager
    async def subscribe(self, after: int = 0) -> AsyncIterator[asyncio.Queue]:
        """A live queue, pre-loaded with any buffered event after ``after``.

        ``after`` is the client's ``Last-Event-ID`` on a reconnect, so a subscriber
        that dropped and came back sees nothing twice and misses nothing that was
        published while it was gone.
        """
        loop = asyncio.get_running_loop()
        sub_queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            for event in self._history:
                if event.seq > after:
                    sub_queue.put_nowait(event)
            closed = self._closed
            if not closed:
                self._subscribers[sub_id] = (loop, sub_queue)
        if closed:
            sub_queue.put_nowait(_CLOSE)
        try:
            yield sub_queue
        finally:
            with self._lock:
                self._subscribers.pop(sub_id, None)
