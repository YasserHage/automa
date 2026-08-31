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

from automa.core.game import (
    LABEL,
    TO_PLAY,
    TURN,
    UNAVAILABLE,
    UNKNOWN,
    Action,
    Player,
    State,
    Viewer,
    unavailable_answer,
)
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


#: A turn can legitimately reveal two unfamiliar cards at once, but not endlessly. This
#: bounds a module whose `resolve_unknown` fails to clear the gap it declared.
MAX_UNKNOWNS_PER_TURN = 8


class MissingInformant(RuntimeError):
    """The module needs a fact and nothing can supply it.

    Raised rather than guessed. A match that invented the answer would be playing a
    different game from the one on the table, and would do it silently.
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
    intervention: bool = False
    """The decider proposed something illegal and the core substituted a legal action.

    Kept distinct from ``fallback`` on purpose. A fallback means the model could not
    be reached; an intervention means it *was* reached and got the rules wrong. Only
    the second is a corrective intervention in the sense the primary metric counts,
    and folding them together would let a provider outage read as a rules failure.
    """


Decider = Callable[[TurnContext], "int | Decision"]

#: Supplies information only the physical table has - what a just-revealed card does,
#: what was drawn, what a die showed.
#:
#: Deliberately **not** a decider. A decider chooses among options the core enumerated;
#: an informant supplies a fact the core does not have and cannot derive. Supplying a
#: fact is neither a decision nor a corrective intervention, so it is logged separately
#: and excluded from the primary metric - otherwise telling the system what a card does
#: would count against it exactly as getting a rule wrong does.
#:
#: Receives the ``unknown`` dict the module put in state, and returns the answer that
#: module's ``resolve_unknown`` will fold back in.
Informant = Callable[[TurnContext, dict[str, Any]], dict[str, Any]]


def uninformed(_context: TurnContext, unknown: dict[str, Any]) -> dict[str, Any]:
    """An :data:`Informant` with nothing to offer: every question goes unanswered.

    Not a placeholder. It is what an unattended match legitimately supplies when there is
    no person at the table, and passing it is meaningfully different from passing nothing
    at all: ``None`` means the caller forgot, and raises; this means nobody could say, and
    the match carries on with the gap on the record. A module that cannot finish under it
    was never fit to be played.
    """
    return unavailable_answer(unknown)


def compose(*informants: Informant) -> Informant:
    """One informant from several, each answering the kinds of question it knows.

    A match at a physical table has two sources at once: the person reads the cards off
    the table, and the catalog says what those cards do. Neither can answer the other's
    questions, and neither should have to know the other exists - so they are asked in
    order and the first real answer wins.

    An informant that cannot help returns :data:`~automa.core.game.UNAVAILABLE`, which
    is exactly what "ask the next one" looks like. When nobody can help, the question is
    answered unavailable - the module's own fallback then applies, as it does for a
    person who cannot say.
    """

    def _ask(ctx: TurnContext, unknown: dict[str, Any]) -> dict[str, Any]:
        for informant in informants:
            answer = informant(ctx, unknown)
            if answer is not None and not answer.get(UNAVAILABLE):
                return answer
        return unavailable_answer(unknown)

    return _ask


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
    interventions: dict[Player, int] = field(default_factory=dict)
    #: Facts supplied by the table. Counted apart from interventions on purpose: being
    #: asked what a card does is not the system getting a rule wrong.
    informed: int = 0
    first_intervention_turn: int | None = None
    """Turns survived before a human would have had to step in - the primary metric."""
    invariant_violations: list[str] = field(default_factory=list)
    final_state: State | None = None
    """The state the match ended on - a plain-play game keeps no score anywhere else.

    ``MatchResult`` deliberately carries no game-specific field (invariant 4), so this
    is opaque to the core; it exists so a caller can render it through the game's own
    `describe_state` - the same call every turn already uses - rather than the result
    having nothing to show for a game whose whole point is its final tally."""

    def summary(self) -> str:
        if self.outcome != "terminal":
            head = f"Match {self.outcome} after {self.turns} turn(s)"
        elif self.winner is None:
            head = f"Draw after {self.turns} turn(s)"
        else:
            head = f"{self.winner} wins after {self.turns} turn(s)"
        notes = []
        if any(self.fallbacks.values()):
            notes.append(f"{sum(self.fallbacks.values())} fallback action(s)")
        if any(self.interventions.values()):
            notes.append(f"{sum(self.interventions.values())} intervention(s)")
        return f"{head} ({', '.join(notes)})" if notes else head


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
        informant: Supplies facts the module says it is missing; see
            :data:`Informant`. A game that can raise `unknown` needs one, or the
            match cannot proceed.
    """
    missing = [p for p in game.PLAYERS if p not in deciders]
    if missing:
        raise ValueError(f"no decider for {missing}; {game.NAME} needs one per player")

    state = game.initial_state(seed) if initial is None else initial
    result = MatchResult(
        game=game.NAME,
        seed=seed,
        outcome="terminal",
        fallbacks={p: 0 for p in game.PLAYERS},
        interventions={p: 0 for p in game.PLAYERS},
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

            # Information first: a module missing a fact cannot meaningfully enumerate
            # what is legal until it has that fact.
            state, asked = _resolve_unknowns(game, state, informant, trajectory, ply)
            result.informed += asked
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
            if decision.intervention:
                result.interventions[player] += 1
                if result.first_intervention_turn is None:
                    result.first_intervention_turn = ctx.turn

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
                intervention=decision.intervention,
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
    result.final_state = state
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
        interventions=result.interventions,
        first_intervention_turn=result.first_intervention_turn,
        informed=result.informed,
    )
    return result


def _resolve_unknowns(
    game: ModuleType,
    state: State,
    informant: Informant | None,
    trajectory: TrajectoryWriter | None,
    ply: int,
) -> tuple[State, int]:
    """Close every gap the module declares, before anyone is asked to decide.

    Loops because answering one question can reveal another - two unfamiliar cards
    turning up on the same turn. Bounded, so a module that never clears its own
    ``unknown`` fails loudly rather than hanging.
    """
    asked = 0
    for _ in range(MAX_UNKNOWNS_PER_TURN):
        unknown = state.get(UNKNOWN)
        if not unknown:
            return state, asked
        if informant is None:
            raise MissingInformant(
                f"{game.NAME} needs information it cannot derive "
                f"({unknown.get('kind')}: {unknown.get('prompt')}), but no informant "
                "was supplied. Pass one to play_match."
            )
        resolve = getattr(game, "resolve_unknown", None)
        if resolve is None:
            raise MissingInformant(
                f"{game.NAME} set {UNKNOWN!r} but does not implement resolve_unknown, "
                "so an answer would have nowhere to go."
            )

        context = TurnContext(
            game=game,
            state=state,
            player=state[TO_PLAY],
            viewer=viewer_for(state[TO_PLAY]),
            actions=[],
            turn=state[TURN],
            ply=ply,
            trajectory=trajectory,
        )
        answer = informant(context, unknown)
        _emit(
            trajectory,
            "table_information",
            ply=ply,
            turn=state[TURN],
            kind=unknown.get("kind"),
            prompt=unknown.get("prompt"),
            answer=answer,
            note="A fact supplied by the table - not a decision, not a correction.",
        )
        state = resolve(state, answer)
        asked += 1

    raise MissingInformant(
        f"{game.NAME} still reports missing information after "
        f"{MAX_UNKNOWNS_PER_TURN} answers on a single turn; resolve_unknown is not "
        "clearing what it declared."
    )


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
