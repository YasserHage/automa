"""A match, run on a worker thread, driven through the web by two queues.

`play_match` (`core/match.py`) is a blocking synchronous loop: it calls a decider
directly and resolves each `unknown` by calling an informant directly. There is no
generator, no `step()`, no coroutine anywhere in the codebase, and re-deriving that
machinery from request handlers would discard `_resolve_unknowns`, the
`IllegalDecision` guard, fallback/intervention accounting and every trajectory event -
in other words, the project. So `play_match` runs unmodified on a daemon thread, and
its callbacks (a decider, two askers, an observer) block on `queue.Queue` objects that
HTTP handlers feed from the outside.

**No module-level mutable state.** `play.py`'s `_LEARNED` and `_UNANSWERED` globals
exist because the CLI serves one match at a time; the web can hold several at once, so
the equivalents live on `MatchSession` itself.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import asdict
from types import ModuleType
from typing import Any

from automa import config
from automa.agents.tactician import tactician_decider
from automa.catalog import store
from automa.catalog.informant import catalog_informant, never_asks
from automa.core.game import AUTOMA, LABEL, PLY, TO_PLAY, TURN, UNAVAILABLE, Viewer
from automa.core.loader import load_game
from automa.core.match import (
    Decision,
    MatchAbandoned,
    TurnContext,
    compose,
    play_match,
    random_decider,
    uninformed,
    viewer_for,
)
from automa.table import table_informant
from automa.trajectory import TrajectoryWriter
from automa.web.paths import catalog_path
from automa.web.streaming import Broadcaster

#: The CLI's own sentence, verbatim (`scripts/play.py`). Load-bearing product copy:
#: it is what tells a person a question is information, not a move being judged.
FRAMING = "This is information, not a move - it is not counted against you."

#: How long the human side of a `MatchSession` may go without a move or an answer
#: before the match is treated as abandoned. Generous: a person hunting for a
#: physical card, or reading a rulebook mid-turn, is legitimately slow. Overridable
#: for tests.
TURN_TIMEOUT_S = float(os.environ.get("AUTOMA_WEB_TURN_TIMEOUT_S", 1800.0))
ANSWER_TIMEOUT_S = float(os.environ.get("AUTOMA_WEB_ANSWER_TIMEOUT_S", 1800.0))

#: Pushed onto a session's queues to end it. A queue holding `None` would be
#: ambiguous with an empty answer ("I cannot say"), so a private sentinel is used.
_ABANDON = object()


class MatchSession:
    """One match, its two queues, and everything the UI needs to render it."""

    def __init__(
        self,
        *,
        session_id: str,
        game: ModuleType,
        mode: str,
        simulate_deal: bool,
        use_catalog: bool,
        seed: int | None,
        delay: float,
        max_plies: int,
        human_player: str,
        automa_player: str,
        module_name: str | None = None,
    ) -> None:
        self.id = session_id
        self.game = game
        #: What `load_game` resolves - the only addressable identity, and what a
        #: reload would re-import. Distinct from `game_name` because two modules may
        #: declare the same `NAME` (`dnup.py` and `dnup_compiled.py` both do).
        self.module_name = module_name or game.NAME
        #: The module's own `NAME`, which is where its catalog lives.
        self.game_name = game.NAME
        self.mode = mode
        self.simulate_deal = simulate_deal
        self.use_catalog = use_catalog
        self.seed = seed
        self.delay = delay
        self.max_plies = max_plies
        self.human_player = human_player
        self.automa_player = automa_player

        self.decisions: queue.Queue = queue.Queue()
        self.answers: queue.Queue = queue.Queue()
        self.broadcaster = Broadcaster()

        self.pending: dict[str, Any] | None = None
        self.status = "running"  # running | finished | abandoned | failed
        self.learned: list[str] = []
        self.unanswered: list[str] = []
        self.result = None
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self.last_seen = time.monotonic()
        self.trajectory_path = None
        self.llm_calls = 0
        self.cost_usd = 0.0

        # Set by `_reporting` for the duration of one question, so the card-effect
        # asker can attach `card_type` to its `question` event - the bare
        # `Asker = str -> str | None` signature never carries it otherwise.
        self.current_kind: str | None = None
        self.current_card_type: str | None = None
        self._abandon_reason: str | None = None

    # -- published to the SSE stream ---------------------------------------

    def publish(self, event_type: str, **data: Any):
        return self.broadcaster.publish(event_type, **data)

    def publish_state(self, state: dict[str, Any]) -> None:
        """Always the human's own view - never borrowed from a `TurnContext` that
        might belong to Automa's turn. See CLAUDE.md's hidden-information rule."""
        view = self.game.describe_state(state, viewer_for(self.human_player))
        self.publish(
            "state", view=view, turn=state.get(TURN), ply=state.get(PLY), to_play=state.get(TO_PLAY)
        )

    def set_pending(self, payload: dict[str, Any]) -> None:
        self.pending = payload

    def clear_pending(self) -> None:
        self.pending = None

    def note_learned(self, card_type: str, stored: bool) -> None:
        self.learned.append(card_type)
        self.publish("learned", card_type=card_type, stored=stored)

    def ask(self, payload: dict[str, Any]) -> str | None:
        """Put a question to the browser and block for its answer.

        Timing out must raise, never return `None`: `catalog_informant` only parks a
        card *after* `ask()` returns, so a `None` on a dropped connection would
        permanently park it as "nobody at the table could describe it" - corrupting
        the catalog for every future match of this game.
        """
        self.publish("question", framing=FRAMING, **payload)
        # The question's own payload is nested rather than spread. It carries its own
        # `kind` - "components" or "card_effect" - which spreading would use to
        # overwrite the "question"/"turn" discriminator the routes dispatch on, so
        # `/answer` would answer 409 for every question ever asked.
        self.set_pending({"kind": "question", "question": payload, "framing": FRAMING})
        try:
            raw = self.answers.get(timeout=ANSWER_TIMEOUT_S)
        except queue.Empty:
            raise MatchAbandoned("no answer received before the timeout") from None
        if raw is _ABANDON:
            raise MatchAbandoned("abandoned while a question was pending")
        self.clear_pending()
        return raw or None

    # -- lifecycle ------------------------------------------------------------

    def finish(self, llm_calls: int, cost_usd: float, trajectory_path: Any) -> None:
        self.llm_calls = llm_calls
        self.cost_usd = cost_usd
        self.trajectory_path = trajectory_path
        self.clear_pending()
        result = self.result
        if result is not None and result.outcome == "abandoned":
            self.status = "abandoned"
            self.publish("abandoned", reason=self._abandon_reason or "idle")
            return
        self.status = "finished"
        unanswered_counts = {k: self.unanswered.count(k) for k in sorted(set(self.unanswered))}
        # MatchResult carries no game-specific field (invariant 4); the final board -
        # scores included, for a game that has them - can only come from the game's own
        # describe_state. Public view: a match-end recap is shown to everyone watching,
        # not rendered from one player's private seat.
        final_view = (
            self.game.describe_state(result.final_state, Viewer.PUBLIC)
            if result is not None and result.final_state is not None
            else None
        )
        self.publish(
            "match_end",
            outcome=result.outcome if result else None,
            winner=result.winner if result else None,
            turns=result.turns if result else 0,
            plies=result.plies if result else 0,
            fallbacks=result.fallbacks if result else {},
            interventions=result.interventions if result else {},
            informed=result.informed if result else 0,
            first_intervention_turn=result.first_intervention_turn if result else None,
            invariant_violations=result.invariant_violations if result else [],
            learned=list(self.learned),
            unanswered=unanswered_counts,
            summary=result.summary() if result else "",
            final_view=final_view,
            llm_calls=llm_calls,
            cost_usd=cost_usd,
            trajectory=str(trajectory_path) if trajectory_path else None,
        )

    def fail(self, exc: BaseException) -> None:
        self.status = "failed"
        self.error = str(exc)
        self.publish("error", kind=type(exc).__name__, message=str(exc))

    def close(self) -> None:
        """Publishes nothing more and unblocks every SSE subscriber - non-negotiable
        in a `finally`, including after a crash."""
        self.broadcaster.close()

    def abandon(self, reason: str = "quit") -> None:
        """Push the abandon sentinel onto both queues. Idempotent."""
        if self.status != "running":
            return
        self._abandon_reason = reason
        self.decisions.put(_ABANDON)
        self.answers.put(_ABANDON)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game": self.module_name,
            "declared_name": self.game_name,
            "mode": self.mode,
            "simulate_deal": self.simulate_deal,
            "use_catalog": self.use_catalog,
            "seed": self.seed,
            "status": self.status,
            "pending": self.pending,
            "human_player": self.human_player,
            "automa_player": self.automa_player,
            "learned": list(self.learned),
            "unanswered": list(self.unanswered),
            "error": self.error,
            "result": asdict(self.result) if self.result is not None else None,
            "llm_calls": self.llm_calls,
            "cost_usd": self.cost_usd,
            "trajectory": str(self.trajectory_path) if self.trajectory_path else None,
        }


# -- the deciders, askers and observer, all queue-backed ---------------------


def _emit_human_checkpoint(ctx: TurnContext, index: int) -> None:
    if ctx.trajectory is not None:
        ctx.trajectory.emit(
            "human_checkpoint",
            event="human_turn",
            ply=ctx.ply,
            turn=ctx.turn,
            shown=ctx.view(),
            options=[a[LABEL] for a in ctx.actions],
            chosen_index=index,
            chosen=ctx.actions[index][LABEL],
        )


def _web_decider(session: MatchSession):
    def decide(ctx: TurnContext) -> Decision:
        view = ctx.view()  # safe here: ctx.viewer is HUMAN by construction
        options = [a[LABEL] for a in ctx.actions]
        while True:
            session.set_pending(
                {"kind": "turn", "turn": ctx.turn, "ply": ctx.ply, "view": view, "options": options}
            )
            session.publish("your_turn", turn=ctx.turn, ply=ctx.ply, view=view, options=options)
            try:
                raw = session.decisions.get(timeout=TURN_TIMEOUT_S)
            except queue.Empty:
                raise MatchAbandoned("no move received before the timeout") from None
            if raw is _ABANDON:
                raise MatchAbandoned("abandoned on the human's turn")
            legal = (
                isinstance(raw, int)
                and not isinstance(raw, bool)
                and 0 <= raw < len(ctx.actions)
            )
            if not legal:
                session.publish(
                    "error",
                    kind="illegal_index",
                    message=f"{raw!r} is not one of 0-{len(ctx.actions) - 1}",
                )
                continue
            _emit_human_checkpoint(ctx, raw)
            session.clear_pending()
            return Decision(index=raw)

    return decide


def _web_card_asker(session: MatchSession):
    def ask(prompt: str) -> str | None:
        return session.ask(
            {"kind": "card_effect", "prompt": prompt, "card_type": session.current_card_type}
        )

    return ask


def _web_component_asker(session: MatchSession):
    def ask(question) -> str | None:
        return session.ask(
            {
                "kind": "components",
                "prompt": question.prompt,
                "count": question.count,
                "options": list(question.options),
                "note": question.note,
            }
        )

    return ask


def _reporting(session: MatchSession, inner):
    def _ask(ctx, unknown: dict[str, Any]) -> dict[str, Any]:
        session.current_kind = unknown.get("kind")
        session.current_card_type = unknown.get("card_type")
        try:
            answer = inner(ctx, unknown)
        finally:
            session.current_kind = None
        if answer.get(UNAVAILABLE):
            session.unanswered.append(str(unknown.get("kind")))
        return answer

    return _ask


def _paced(decider, seconds: float):
    if seconds <= 0:
        return decider

    def _decide(ctx: TurnContext):
        time.sleep(seconds)
        return decider(ctx)

    return _decide


def _announcing(session: MatchSession, decider):
    def _decide(ctx: TurnContext):
        session.publish_state(ctx.state)
        session.publish("thinking", who="automa", turn=ctx.turn)
        return decider(ctx)

    return _decide


def _observer(session: MatchSession):
    def observe(ctx: TurnContext, decision: Decision, action: dict[str, Any]) -> None:
        if ctx.player == session.automa_player:
            session.publish(
                "automa_moved", turn=ctx.turn, action=action[LABEL], fallback=decision.fallback
            )

    return observe


def _run(session: MatchSession) -> None:
    try:
        with TrajectoryWriter(
            session.game_name, config=config.describe(), seeds={"match": session.seed}
        ) as traj:
            session.trajectory_path = traj.path
            channels = []
            if not session.simulate_deal:
                channels.append(table_informant(_web_component_asker(session)))
            catalog = None
            if session.use_catalog:
                catalog = store.load(session.game_name, catalog_path(session.game_name))
                channels.append(
                    catalog_informant(
                        catalog,
                        ask=_web_card_asker(session) if session.mode == "live" else never_asks,
                        trajectory=traj,
                        save=(session.mode == "live"),
                        on_learn=session.note_learned,
                    )
                )
            informant = _reporting(session, compose(*channels)) if channels else uninformed
            automa = (
                tactician_decider() if session.mode == "live" else random_decider(session.seed)
            )
            deciders = {
                session.automa_player: _announcing(session, _paced(automa, session.delay)),
                session.human_player: _web_decider(session),
            }

            session.publish(
                "match_start",
                game=session.game_name,
                seed=session.seed,
                mode=session.mode,
                simulate_deal=session.simulate_deal,
                human_player=session.human_player,
                automa_player=session.automa_player,
                model=config.model_for("tactician"),
                catalog=(
                    {
                        "known": len(catalog.known()),
                        "parked": len(catalog.parked()),
                        "path": str(catalog.path),
                    }
                    if catalog is not None
                    else None
                ),
            )

            session.result = play_match(
                session.game,
                deciders,
                seed=session.seed,
                trajectory=traj,
                observer=_observer(session),
                informant=informant,
                max_plies=session.max_plies,
            )
            session.finish(traj.llm_calls, traj.total_cost_usd, traj.path)
    except BaseException as exc:  # noqa: BLE001 - the trajectory's own error event
        session.fail(exc)
    finally:
        session.close()


# -- the registry --------------------------------------------------------------


class TooManySessionsError(RuntimeError):
    """The registry's running-session cap is already reached."""


class SessionNotFoundError(KeyError):
    """No session with this id (never created, or evicted after finishing)."""


class SessionRegistry:
    """Every live and recently-finished `MatchSession`, plus a janitor thread.

    No module-level mutable state: `create_app()` constructs exactly one of these
    and hangs it on `app.state`, so tests can build their own registry with nothing
    shared between them.
    """

    def __init__(
        self,
        *,
        max_running: int = 4,
        idle_timeout: float = 3600.0,
        finished_ttl: float = 3600.0,
        janitor_interval: float = 30.0,
        start_janitor: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, MatchSession] = {}
        self._max_running = max_running
        self._idle_timeout = idle_timeout
        self._finished_ttl = finished_ttl
        self._stop = threading.Event()
        self._janitor: threading.Thread | None = None
        if start_janitor:
            self._janitor = threading.Thread(
                target=self._run_janitor, args=(janitor_interval,), daemon=True,
                name="automa-web-session-janitor",
            )
            self._janitor.start()

    def create(
        self,
        *,
        game_name: str,
        mode: str,
        simulate_deal: bool,
        use_catalog: bool,
        seed: int | None,
        delay: float,
        max_plies: int,
    ) -> MatchSession:
        with self._lock:
            running = sum(1 for s in self._sessions.values() if s.status == "running")
            if running >= self._max_running:
                raise TooManySessionsError(
                    f"{self._max_running} session(s) already running; "
                    "finish or abandon one first"
                )

        game = load_game(game_name)
        automa_player = AUTOMA if AUTOMA in game.PLAYERS else game.PLAYERS[0]
        human_player = next(p for p in game.PLAYERS if p != automa_player)

        session = MatchSession(
            session_id=uuid.uuid4().hex[:12],
            game=game,
            mode=mode,
            simulate_deal=simulate_deal,
            use_catalog=use_catalog,
            seed=seed,
            delay=delay,
            max_plies=max_plies,
            human_player=human_player,
            automa_player=automa_player,
            module_name=game_name,
        )
        session.thread = threading.Thread(
            target=_run, args=(session,), daemon=True, name=f"automa-web-match-{session.id}"
        )
        with self._lock:
            self._sessions[session.id] = session
        session.thread.start()
        return session

    def get(self, session_id: str) -> MatchSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def touch(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_seen = time.monotonic()

    def abandon(self, session_id: str, reason: str = "quit") -> None:
        self.get(session_id).abandon(reason)

    def is_module_busy(self, module_name: str) -> bool:
        """Whether a live session holds this *module* object.

        What the reload guard asks: `importlib.reload` mutates the module in place,
        so reloading one a running match is calling into would swap the rules out
        mid-turn.
        """
        with self._lock:
            return any(
                s.module_name == module_name and s.status == "running"
                for s in self._sessions.values()
            )

    def is_catalog_busy(self, game_name: str) -> bool:
        """Whether a live session is writing this game's *catalog file*.

        A different question from :meth:`is_module_busy`, and it must be asked by the
        module's declared `NAME`: two installed modules can share one, and they then
        share `catalog/<NAME>.json` too - so a match under either one can overwrite an
        edit made through the editor.
        """
        with self._lock:
            return any(
                s.game_name == game_name and s.status == "running"
                for s in self._sessions.values()
            )

    def _run_janitor(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self._sweep()

    def _sweep(self) -> None:
        now = time.monotonic()
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if (
                session.status == "running"
                and not session.broadcaster.has_subscribers()
                and now - session.last_seen > self._idle_timeout
            ):
                session.abandon("idle")
        with self._lock:
            stale = [
                sid
                for sid, s in self._sessions.items()
                if s.status != "running" and now - s.last_seen > self._finished_ttl
            ]
            for sid in stale:
                self._sessions.pop(sid, None)

    def shutdown(self) -> None:
        """Drain every running session so its trajectory gets a clean `run_end`."""
        self._stop.set()
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.status == "running":
                session.abandon("shutdown")
        for session in sessions:
            if session.thread is not None:
                session.thread.join(timeout=5.0)
