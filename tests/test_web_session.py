"""A match driven through the web's queues, entirely offline.

The important test file for the web layer. Everything hard in the design - the
cross-thread hand-off, the abandon sentinel, an out-of-range index being re-prompted
rather than killing the match, the daemon-thread drain - is exercised here against
Nim: no cards, no hidden state, no LLM call, and nothing that depends on which of the
other games happens to be installed.

No socket is opened and no port is bound: the session layer is driven directly, the
way `tests/test_play_cli.py` already drives the CLI's deciders and observer.
"""

from __future__ import annotations

import queue
import time
from typing import Any

import pytest

from automa.core.loader import load_game
from automa.web.services.session import SessionRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A registry whose sessions write their trajectories into `tmp_path`.

    The janitor is off: these tests drive the lifecycle explicitly, and a background
    thread sweeping mid-assertion would make failures non-reproducible.
    """
    monkeypatch.setenv("AUTOMA_TRAJECTORY_DIR", str(tmp_path / "trajectories"))
    import automa.config as config_module

    monkeypatch.setattr(config_module, "TRAJECTORY_DIR", tmp_path / "trajectories")
    reg = SessionRegistry(start_janitor=False)
    try:
        yield reg
    finally:
        reg.shutdown()


def offline_nim(registry: SessionRegistry, **kw: Any):
    """An offline Nim session: random opponent, module deals, no catalog, no model."""
    options = {
        "game_name": "nim",
        "mode": "offline",
        "simulate_deal": True,
        "use_catalog": False,
        "seed": 7,
        "delay": 0.0,
        "max_plies": 1000,
    }
    options.update(kw)
    return registry.create(**options)


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.01):
    """Poll until `predicate()` is truthy. Returns it, or raises on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def pending_turn(session):
    return wait_for(
        lambda: session.pending if (session.pending or {}).get("kind") == "turn" else None
    )


def published(session) -> list[dict[str, Any]]:
    return [
        {"type": e.type, **e.data}
        for e in session.broadcaster._history  # noqa: SLF001 - the log is the assertion
    ]


def test_an_offline_match_plays_to_a_terminal_state(registry: SessionRegistry) -> None:
    session = offline_nim(registry)

    while True:
        wait_for(lambda: session.pending is not None or session.status != "running")
        if session.status != "running":
            break
        turn = session.pending
        session.decisions.put(0)  # always the first legal action
        assert turn["options"], "the core enumerated the options; the UI never invents one"

    wait_for(lambda: session.status == "finished")
    assert session.result is not None
    assert session.result.outcome == "terminal"
    assert session.result.winner in {"automa", "human"}

    types = [e["type"] for e in published(session)]
    assert types[0] == "match_start"
    assert types[-1] == "match_end"
    assert "your_turn" in types and "automa_moved" in types


def test_an_out_of_range_index_is_refused_and_the_match_continues(
    registry: SessionRegistry,
) -> None:
    """The re-loop is load-bearing.

    An out-of-range index reaching `play_match` raises `IllegalDecision`, which is NOT
    caught by its `except MatchAbandoned` - the match would die with no `match_end`
    event at all. So the decider re-prompts instead, exactly as the CLI does.
    """
    session = offline_nim(registry)
    turn = pending_turn(session)

    session.decisions.put(len(turn["options"]) + 99)
    wait_for(lambda: any(e["type"] == "error" for e in published(session)))
    error = next(e for e in published(session) if e["type"] == "error")
    assert error["kind"] == "illegal_index"
    assert session.status == "running", "the match survives a bad index"

    # And the same turn is offered again rather than skipped.
    session.decisions.put(0)
    wait_for(lambda: session.status != "running" or session.pending is not None)
    assert session.status in {"running", "finished"}


def test_a_negative_index_is_refused_too(registry: SessionRegistry) -> None:
    session = offline_nim(registry)
    pending_turn(session)
    session.decisions.put(-1)
    wait_for(lambda: any(e["type"] == "error" for e in published(session)))
    assert session.status == "running"


def test_a_boolean_is_not_an_index(registry: SessionRegistry) -> None:
    """`True` is an `int` in Python, and `0 <= True < n` is usually true.

    Accepting it would silently play action 1 whenever a client sent `true`.
    """
    session = offline_nim(registry)
    pending_turn(session)
    session.decisions.put(True)
    wait_for(lambda: any(e["type"] == "error" for e in published(session)))
    assert session.status == "running"


def test_abandoning_ends_the_match_and_closes_the_stream(registry: SessionRegistry) -> None:
    session = offline_nim(registry)
    pending_turn(session)

    session.abandon("quit")

    wait_for(lambda: session.status == "abandoned")
    assert session.result is not None
    assert session.result.outcome == "abandoned"
    assert any(e["type"] == "abandoned" for e in published(session))
    assert not session.broadcaster.has_subscribers()


def test_the_trajectory_is_closed_even_on_an_abandon(registry: SessionRegistry) -> None:
    """`TrajectoryWriter.__exit__` must run, or the log has no `run_end`.

    A truncated trajectory is a direct hit on invariant 3's audit value, and the
    abandon path is exactly where it would be lost.
    """
    import json

    session = offline_nim(registry)
    pending_turn(session)
    session.abandon("quit")
    wait_for(lambda: session.status == "abandoned")
    wait_for(lambda: session.trajectory_path and session.trajectory_path.exists())

    events = [
        json.loads(line)
        for line in session.trajectory_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert events[0]["type"] == "run_start"
    assert events[-1]["type"] == "run_end", "the writer's context manager ran"


def test_the_session_holds_its_own_counters_not_module_globals(
    registry: SessionRegistry,
) -> None:
    """`play.py`'s `_LEARNED`/`_UNANSWERED` are module globals; the web has several
    matches at once, so the equivalents must live on the session."""
    from pathlib import Path

    import automa.web.services.session as module

    first = offline_nim(registry)
    second = offline_nim(registry, seed=11)
    try:
        assert first.learned is not second.learned
        assert first.unanswered is not second.unanswered
    finally:
        first.abandon()
        second.abandon()

    # And the web never reaches for the CLI's terminal-shaped helpers at all - the
    # ~90 lines are reimplemented against queues instead, globals included.
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "automa.scripts.play" not in source


def test_the_shutdown_drain_stops_every_running_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    reg = SessionRegistry(start_janitor=False)
    session = offline_nim(reg)
    pending_turn(session)

    reg.shutdown()

    assert session.status in {"abandoned", "finished"}
    assert session.thread is not None and not session.thread.is_alive()


def test_a_session_is_capped(tmp_path, monkeypatch) -> None:
    from automa.web.services.session import TooManySessionsError

    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    reg = SessionRegistry(max_running=1, start_janitor=False)
    try:
        offline_nim(reg)
        with pytest.raises(TooManySessionsError):
            offline_nim(reg, seed=2)
    finally:
        reg.shutdown()


def test_a_components_question_answered_by_abandoning_writes_no_catalog(
    tmp_path, monkeypatch
) -> None:
    """Timing out or abandoning an ask must RAISE, never return `None`.

    `catalog_informant` parks a card only *after* `ask()` returns, so a `None` on a
    dropped connection would permanently park it as "nobody at the table could
    describe it" - and a parked card is never asked about again, corrupting the Phase
    5 question count for every future match of that game.
    """
    from automa.core.match import MatchAbandoned
    from automa.web.services.session import _ABANDON, MatchSession

    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    session = MatchSession(
        session_id="t",
        game=load_game("nim"),
        mode="live",
        simulate_deal=False,
        use_catalog=True,
        seed=1,
        delay=0.0,
        max_plies=10,
        human_player="human",
        automa_player="automa",
    )
    session.answers.put(_ABANDON)
    with pytest.raises(MatchAbandoned):
        session.ask({"kind": "card_effect", "prompt": "what is this?", "card_type": "x"})


def test_a_timed_out_ask_raises_rather_than_parking_a_card(tmp_path, monkeypatch) -> None:
    from automa.core.match import MatchAbandoned
    from automa.web.services import session as session_module

    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    monkeypatch.setattr(session_module, "ANSWER_TIMEOUT_S", 0.05)
    session = session_module.MatchSession(
        session_id="t",
        game=load_game("nim"),
        mode="live",
        simulate_deal=False,
        use_catalog=True,
        seed=1,
        delay=0.0,
        max_plies=10,
        human_player="human",
        automa_player="automa",
    )
    with pytest.raises(MatchAbandoned):
        session.ask({"kind": "card_effect", "prompt": "?", "card_type": "x"})


def test_an_empty_answer_is_a_real_answer(tmp_path, monkeypatch) -> None:
    """"I cannot say" is always permitted, and arrives as `None` for the informant."""
    from automa.web.services import session as session_module

    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    session = session_module.MatchSession(
        session_id="t",
        game=load_game("nim"),
        mode="live",
        simulate_deal=False,
        use_catalog=False,
        seed=1,
        delay=0.0,
        max_plies=10,
        human_player="human",
        automa_player="automa",
    )
    session.answers.put("")
    assert session.ask({"kind": "components", "prompt": "?", "count": 2}) is None


def test_pending_says_which_of_the_two_things_it_is_waiting_for(
    tmp_path, monkeypatch
) -> None:
    """Regression, and a nasty one.

    `pending["kind"]` is the discriminator the `/move` and `/answer` routes dispatch
    on: "turn" or "question". A question's own payload also carries a `kind` -
    "components" or "card_effect" - and building the pending dict by spreading that
    payload let the inner value overwrite the outer one. `/answer` then saw
    `kind == "components"`, decided no question was waiting, and returned 409 for
    every question in every match: nothing could ever be answered from the browser.

    The service tests missed it because they call `session.ask()` and check what it
    returns, never the `pending` shape the route validates against - so this asserts
    the shape itself.
    """
    import threading

    from automa.web.services import session as session_module

    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    session = session_module.MatchSession(
        session_id="t",
        game=load_game("nim"),
        mode="live",
        simulate_deal=False,
        use_catalog=False,
        seed=1,
        delay=0.0,
        max_plies=10,
        human_player="human",
        automa_player="automa",
    )

    seen: dict[str, Any] = {}
    ready = threading.Event()

    def answer_once() -> None:
        wait_for(lambda: session.pending is not None)
        seen.update(session.pending)
        ready.set()
        session.answers.put("a b")

    threading.Thread(target=answer_once, daemon=True).start()
    session.ask({"kind": "components", "prompt": "read them out", "count": 2, "options": ["a"]})
    ready.wait(timeout=5)

    assert seen["kind"] == "question", "the outer discriminator must survive"
    assert seen["question"]["kind"] == "components", "and the inner kind is still there"
    assert seen["question"]["count"] == 2
    assert seen["question"]["prompt"] == "read them out"


def test_the_question_event_carries_the_clis_own_framing_sentence(
    tmp_path, monkeypatch
) -> None:
    """Load-bearing product copy, not filler: it is what tells a person a question is
    information rather than a move being judged."""
    from automa.web.services import session as session_module

    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    session = session_module.MatchSession(
        session_id="t",
        game=load_game("nim"),
        mode="live",
        simulate_deal=False,
        use_catalog=False,
        seed=1,
        delay=0.0,
        max_plies=10,
        human_player="human",
        automa_player="automa",
    )
    session.answers.put("a b")
    session.ask({"kind": "components", "prompt": "read them out", "count": 2})
    question = next(e for e in published(session) if e["type"] == "question")
    assert question["framing"] == (
        "This is information, not a move - it is not counted against you."
    )


def test_the_registry_reports_a_game_as_busy_while_a_match_runs(
    registry: SessionRegistry,
) -> None:
    """What the 409 guards are built on, and they ask two different questions.

    `is_module_busy` guards a *reload* - `importlib.reload` mutates the module object
    a running match is calling into. `is_catalog_busy` guards a catalog *write* - a
    live `catalog_informant(save=True)` rewrites the whole file. They are keyed
    differently on purpose, because two installed modules can declare one `NAME` and
    therefore share one catalog file while remaining separately loadable.
    """
    assert not registry.is_module_busy("nim")
    assert not registry.is_catalog_busy("nim")

    session = offline_nim(registry)
    pending_turn(session)

    assert registry.is_module_busy("nim")
    assert registry.is_catalog_busy("nim")
    assert session.module_name == "nim"
    assert session.game_name == "nim"

    session.abandon()
    wait_for(lambda: not registry.is_module_busy("nim"))


def test_a_module_is_addressed_by_its_module_name_not_its_declared_name(
    registry: SessionRegistry,
) -> None:
    """`dnup.py` and `dnup_compiled.py` both declare `NAME = "dnup"`.

    Keying on `NAME` made one of the two unreachable and let a reload of the other
    slip past the busy guard. The module name is what `load_game` resolves, so it is
    the identity; `NAME` is only where the catalog lives.
    """
    from automa.web.services.games import loadable_games

    if "dnup_compiled" not in loadable_games():
        pytest.skip("dnup_compiled is not installed")

    session = registry.create(
        game_name="dnup_compiled",
        mode="offline",
        simulate_deal=True,
        use_catalog=False,
        seed=5,
        delay=0.0,
        max_plies=5,
    )
    try:
        assert session.module_name == "dnup_compiled"
        assert session.game_name == "dnup", "its catalog still follows its declared NAME"
        assert registry.is_module_busy("dnup_compiled")
        assert not registry.is_module_busy("dnup"), "a different module is not busy"
        assert registry.is_catalog_busy("dnup"), "but they do share one catalog file"
    finally:
        session.abandon()


def test_queues_are_per_session(registry: SessionRegistry) -> None:
    a = offline_nim(registry)
    b = offline_nim(registry, seed=3)
    try:
        assert isinstance(a.decisions, queue.Queue)
        assert a.decisions is not b.decisions
        assert a.answers is not b.answers
        assert a.broadcaster is not b.broadcaster
    finally:
        a.abandon()
        b.abandon()
