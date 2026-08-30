"""The broadcaster, and the invariant-3 guard on the trajectory subclass.

`compile_game` exposes no progress callback, so the web subclasses `TrajectoryWriter`
to publish on `emit()`. That is only acceptable if the file it writes is unchanged -
invariant 3 says every prompt sent and every response received are logged, and a
subclass that altered the log would quietly weaken the project's central audit trail.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from automa.trajectory import TrajectoryWriter
from automa.web.services.compiling import BroadcastingTrajectory
from automa.web.streaming import Broadcaster, is_close

EVENTS = [
    ("llm_call", {"agent": "compiler", "input_tokens": 10, "output_tokens": 4}),
    ("core_event", {"event": "compile_attempt", "attempt": 1, "ok": False}),
    ("core_event", {"event": "compile_attempt", "attempt": 2, "ok": True}),
]

#: Fields that legitimately differ between two runs and are not part of the format.
_VOLATILE = {"ts", "run_id", "wall_ms", "git_dirty", "git_commit"}


def _written(path: Path) -> list[dict]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for record in records:
        for key in _VOLATILE:
            record.pop(key, None)
        if isinstance(record.get("config"), dict):
            record["config"] = "<config>"
        record.pop("seeds", None)
    return records


def test_the_broadcasting_writer_produces_the_same_jsonl_as_a_plain_one(
    tmp_path: Path,
) -> None:
    with TrajectoryWriter("x", directory=tmp_path / "plain", run_id="fixed") as plain:
        for event_type, fields in EVENTS:
            plain.emit(event_type, **fields)
        plain_path = plain.path

    broadcaster = Broadcaster()
    with BroadcastingTrajectory(
        "x", directory=tmp_path / "broadcast", run_id="fixed", broadcaster=broadcaster
    ) as loud:
        for event_type, fields in EVENTS:
            loud.emit(event_type, **fields)
        loud_path = loud.path

    assert _written(plain_path) == _written(loud_path)


def test_every_written_event_is_also_published(tmp_path: Path) -> None:
    broadcaster = Broadcaster()
    with BroadcastingTrajectory(
        "x", directory=tmp_path, broadcaster=broadcaster
    ) as traj:
        for event_type, fields in EVENTS:
            traj.emit(event_type, **fields)

    published = [e for e in broadcaster._history if e.type == "trajectory"]  # noqa: SLF001
    types = [e.data["type"] for e in published]
    assert "run_start" in types and "run_end" in types
    assert types.count("core_event") == 2
    assert [e.data["seq"] for e in published] == sorted(e.data["seq"] for e in published)


def test_the_sequence_numbers_the_writer_returns_are_unchanged(tmp_path: Path) -> None:
    """`emit` returns a seq; a subclass that broke that would break every caller."""
    broadcaster = Broadcaster()
    with BroadcastingTrajectory("x", directory=tmp_path, broadcaster=broadcaster) as traj:
        first = traj.emit("note", text="a")
        second = traj.emit("note", text="b")
    assert second == first + 1


# -- the broadcaster itself ---------------------------------------------------


def test_history_is_replayed_from_a_last_event_id() -> None:
    """What makes an SSE reconnect free: nothing twice, nothing missed."""
    broadcaster = Broadcaster()
    for i in range(5):
        broadcaster.publish("tick", n=i)

    async def collect() -> list[int]:
        async with broadcaster.subscribe(after=2) as events:
            return [events.get_nowait().data["n"] for _ in range(3)]

    assert asyncio.run(collect()) == [2, 3, 4]


def test_a_publish_from_another_thread_reaches_a_live_subscriber() -> None:
    """The cross-thread hand-off: a worker thread publishes, the event loop receives.

    A blocking `queue.Queue.get()` must never run inside an async generator, so the
    broadcaster hands each item to the subscriber's own loop with
    `call_soon_threadsafe` instead.
    """
    broadcaster = Broadcaster()

    async def run() -> str:
        async with broadcaster.subscribe() as events:
            threading.Thread(
                target=lambda: broadcaster.publish("from_thread", value="hello"), daemon=True
            ).start()
            event = await asyncio.wait_for(events.get(), timeout=5)
            return event.data["value"]

    assert asyncio.run(run()) == "hello"


def test_closing_wakes_every_subscriber() -> None:
    """`session.close()` in a `finally` is what unblocks a stream after a crash."""
    broadcaster = Broadcaster()

    async def run() -> bool:
        async with broadcaster.subscribe() as events:
            threading.Thread(target=broadcaster.close, daemon=True).start()
            return is_close(await asyncio.wait_for(events.get(), timeout=5))

    assert asyncio.run(run())


def test_subscribing_to_an_already_closed_stream_ends_immediately() -> None:
    """A browser connecting after the match finished must get history, then an end."""
    broadcaster = Broadcaster()
    broadcaster.publish("match_end", outcome="terminal")
    broadcaster.close()

    async def run() -> list[object]:
        async with broadcaster.subscribe() as events:
            return [events.get_nowait(), events.get_nowait()]

    first, second = asyncio.run(run())
    assert first.type == "match_end"
    assert is_close(second)


def test_a_subscriber_is_dropped_when_its_stream_ends() -> None:
    broadcaster = Broadcaster()

    async def run() -> None:
        async with broadcaster.subscribe():
            assert broadcaster.has_subscribers()

    asyncio.run(run())
    assert not broadcaster.has_subscribers(), "dead streams must not accumulate"
