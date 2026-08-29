"""The match loop: deterministic, game-agnostic, and model-agnostic.

The loop enumerates the legal actions from the game module, hands that list to
whoever is deciding this turn, receives an **index** back, and applies the action
itself. Nothing else is accepted. That is invariant 1 expressed as a type rather than
as a rule someone has to remember: a decider cannot express an illegal action,
because the only thing it can return is a position in a list the core built.

Keeping the loop here - rather than inside the agent that calls a model - is what
lets ``tests/test_core_is_llm_free.py`` keep passing. The tactician imports
:mod:`automa.llm`; this module does not, and never may.

The same function serves three phases. Phase 2 wires a human against a tactician,
Phase 3 wires a baseline against the agent over a fixed battery, and Phase 6 wires
two tacticians to measure whether a strategist helps. Only the deciders change.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from automa.core.game import LABEL, TO_PLAY, TURN, Action, Player, State, Viewer
from automa.trajectory.writer import TrajectoryWriter

#: Matches are bounded for the same reason self-play playouts are: a game whose rules
#: impose no ceiling on length still has to stop. See :mod:`automa.core.selfplay`.
DEFAULT_MAX_PLIES = 1000


class IllegalDecision(RuntimeError):
    """A decider returned something that is not an index into the legal actions.

    Raised rather than clamped. A decider reaching outside the list is a bug in that
    decider, and silently playing a neighbouring action would hide the bug and
    corrupt the match at the same time.
    """


class MatchAbandoned(Exception):
    """Raised by a decider to end the match early - the human quitting, typically."""


@dataclass(frozen=True)
class TurnContext:
    """Everything a decider is allowed to know this turn.

    ``viewer`` is the lens the decider must render the state through. The core passes
    it rather than letting the decider pick, so the privacy boundary is set by the
    party that has no interest in crossing it.
    """

    game: ModuleType
    state: State
    player: Player
    viewer: Viewer
    actions: list[Action]
    turn: int
    ply: int
    trajectory: TrajectoryWriter | None = None

    def numbered(self) -> str:
        """The legal actions as a numbered list - what a decider chooses from."""
        return "\n".join(f"{i}. {a[LABEL]}" for i, a in enumerate(self.actions))

    def view(self) -> str:
        return self.game.describe_state(self.state, self.viewer)


@dataclass(frozen=True)
class Decision:
    """A chosen index, plus whatever the decider wants on the record.

    A decider may return a bare ``int`` instead and the loop normalises it. The richer
    form exists so a tactician can carry its justification through to the interface,
    and so a fallback is counted rather than passing as an ordinary choice.
    """

    index: int
    justification: str | None = None
    fallback: bool = False


Decider = Callable[[TurnContext], "int | Decision"]

#: Reserved for Phase 5, and unused here.
#:
#: An informant supplies information only the physical table has - the card Automa
#: just drew, a die roll, a revealed bid. It is deliberately *not* a decider:
#: supplying a fact is neither a decision nor a corrective intervention, and counting
#: it as either would corrupt the primary metric. The schema by which a module
#: declares what it is missing gets designed in Phase 5 against a game that actually
#: draws cards; passing one before then raises rather than silently doing nothing.
Informant = Callable[[TurnContext], Any]

#: Called after each applied action, for interfaces that want to narrate the match.
#: Keeps terminal I/O out of the deterministic core.
Observer = Callable[[TurnContext, Decision, Action], None]


@dataclass
class MatchResult:
    game: str
    seed: int | None
    outcome: str  # "terminal" | "aborted" | "abandoned"
    winner: Player | None = None
    turns: int = 0
    plies: int = 0
    fallbacks: dict[Player, int] = field(default_factory=dict)
    invariant_violations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.outcome != "terminal":
            head = f"Match {self.outcome} after {self.turns} turn(s)"
        elif self.winner is None:
            head = f"Draw after {self.turns} turn(s)"
        else:
            head = f"{self.winner} wins after {self.turns} turn(s)"
        if any(self.fallbacks.values()):
            return f"{head} ({sum(self.fallbacks.values())} fallback action(s))"
        return head


def viewer_for(player: Player) -> Viewer:
    """The lens a player sees the state through.

    Falls back to the public view for a game naming its players something other than
    the canonical two, so an unusual module loses information rather than leaking it.
    """
    try:
        return Viewer(player)
    except ValueError:
        return Viewer.PUBLIC


def random_decider(seed: int | None = None) -> Decider:
    """Uniform choice among the legal actions.

    The opponent for an unattended run, and the fallback when a model cannot be
    reached. Seeded, so an unattended match stays reproducible.
    """
    rng = random.Random(seed)

    def _decide(ctx: TurnContext) -> int:
        return rng.randrange(len(ctx.actions))

    return _decide


def play_match(
    game: ModuleType,
    deciders: dict[Player, Decider],
    *,
    seed: int | None = None,
    trajectory: TrajectoryWriter | None = None,
    initial: State | None = None,
    informant: Informant | None = None,
    observer: Observer | None = None,
    max_plies: int = DEFAULT_MAX_PLIES,
) -> MatchResult:
    """Play one match to a terminal state, logging every step.

    Args:
        deciders: One per player in ``game.PLAYERS``.
        initial: Start from this state instead of ``game.initial_state(seed)``.
            Reserved for Phase 7, which starts a match from the hand actually dealt
            at the physical table rather than a simulated one.
        informant: Reserved for Phase 5; see :data:`Informant`.
    """
    if informant is not None:
        raise NotImplementedError(
            "informant is reserved for Phase 5, when a game with a face-down deck "
            "makes the per-turn information loop real. See automa.core.match.Informant."
        )
    missing = [p for p in game.PLAYERS if p not in deciders]
    if missing:
        raise ValueError(f"no decider for {missing}; {game.NAME} needs one per player")

    state = game.initial_state(seed) if initial is None else initial
    result = MatchResult(
        game=game.NAME,
        seed=seed,
        outcome="terminal",
        fallbacks={p: 0 for p in game.PLAYERS},
    )
    _emit(
        trajectory,
        "match_start",
        game=game.NAME,
        seed=seed,
        players=list(game.PLAYERS),
        from_supplied_state=initial is not None,
    )

    check = getattr(game, "check_invariants", None)
    ply = 0
    try:
        while ply < max_plies:
            if game.is_terminal(state):
                break

            if check is not None:
                for problem in check(state):
                    result.invariant_violations.append(problem)
                    _emit(trajectory, "invariant_violation", ply=ply, problem=problem)

            player = state[TO_PLAY]
            actions = game.legal_actions(state)
            if not actions:
                raise IllegalDecision(
                    f"{game.NAME} offered no legal action to {player} in a "
                    "non-terminal state"
                )

            ctx = TurnContext(
                game=game,
                state=state,
                player=player,
                viewer=viewer_for(player),
                actions=actions,
                turn=state[TURN],
                ply=ply,
                trajectory=trajectory,
            )
            # Logged *before* anyone is consulted: this event is the receipt for
            # invariant 1, showing the options existed independently of the choice.
            _emit(
                trajectory,
                "legal_actions_enumerated",
                ply=ply,
                turn=ctx.turn,
                to_play=player,
                actions=[a[LABEL] for a in actions],
            )

            decision = _normalise(deciders[player](ctx))
            if not 0 <= decision.index < len(actions):
                raise IllegalDecision(
                    f"{player} chose index {decision.index}, but only "
                    f"0..{len(actions) - 1} were legal this turn"
                )
            if decision.fallback:
                result.fallbacks[player] += 1

            action = actions[decision.index]
            state = game.apply(state, action)
            _emit(
                trajectory,
                "action_applied",
                ply=ply,
                turn=ctx.turn,
                player=player,
                index=decision.index,
                action=action[LABEL],
                justification=decision.justification,
                fallback=decision.fallback,
            )
            if observer is not None:
                observer(ctx, decision, action)
            ply += 1
        else:
            result.outcome = "aborted"
    except MatchAbandoned:
        result.outcome = "abandoned"

    result.plies = ply
    result.turns = state[TURN] if isinstance(state, dict) else 0
    if result.outcome == "terminal":
        if game.is_terminal(state):
            result.winner = game.winner(state)
        else:
            result.outcome = "aborted"

    _emit(
        trajectory,
        "match_end",
        outcome=result.outcome,
        winner=result.winner,
        turns=result.turns,
        plies=result.plies,
        fallbacks=result.fallbacks,
    )
    return result


def _normalise(decision: int | Decision) -> Decision:
    if isinstance(decision, Decision):
        return decision
    if isinstance(decision, bool) or not isinstance(decision, int):
        raise IllegalDecision(
            "a decider must return an action index or a Decision, got "
            f"{type(decision).__name__}"
        )
    return Decision(index=decision)


def _emit(trajectory: TrajectoryWriter | None, event: str, **fields: Any) -> None:
    if trajectory is not None:
        trajectory.emit("core_event", event=event, **fields)
