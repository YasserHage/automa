"""The game module contract.

Every game - hand-written in Phase 1, compiler-generated in Phase 4 - implements
this interface, and the core knows nothing else about any game. That is what makes
the match loop in Phase 2 game-agnostic, and what gives the compiler a fixed target
to generate against.

State and actions are plain JSON-round-trippable dicts rather than dataclasses,
which is a choice about what happens downstream: Phase 3 needs seeded fixture states
with ground truth, Phase 7 needs to show state to a human and take corrections back,
and Phase 4 needs a model to emit conforming modules reliably. Nested frozen
dataclasses are pleasant to hand-write and materially worse at all three.

The interface is *structural*: a game is a module, not an instance. Every member of
:class:`GameModule` is therefore a ``staticmethod`` - without that, a type checker
reads the first parameter as a receiver and no module can ever match.
"""

from __future__ import annotations

import inspect
from enum import StrEnum
from types import ModuleType
from typing import Any, Protocol, runtime_checkable

#: A full game state. JSON round-trippable: dicts, lists, str, int, float, bool, None.
State = dict[str, Any]

#: One legal action. JSON round-trippable, and always carries :data:`LABEL`.
Action = dict[str, Any]

#: A player identifier, drawn from a module's ``PLAYERS``.
Player = str


# -- canonical players -----------------------------------------------------
#
# Every game in this project is Automa against the person at the table, so the two
# names are fixed here rather than reinvented per game. The core still reads
# ``module.PLAYERS`` and never assumes these values - a game is free to name its
# players otherwise, and the loader will accept it.

AUTOMA: Player = "automa"
HUMAN: Player = "human"
DEFAULT_PLAYERS: tuple[Player, Player] = (AUTOMA, HUMAN)


# -- core-owned state keys -------------------------------------------------

#: Whose decision the state is waiting on. Always a member of ``PLAYERS``.
TO_PLAY = "to_play"

#: Player turns taken so far. Non-decreasing; unchanged while a pending decision
#: is being resolved, because resolving one is part of a turn, not a turn of its own.
TURN = "turn"

#: Applications of ``apply`` so far. Strictly increasing, always. This is what
#: bounds a match: ``turn`` cannot serve, since multi-step actions advance the
#: state without advancing the turn.
PLY = "ply"

#: Optional. When present and not ``None``, the state is mid-action and waiting on a
#: sub-decision - keeping one of three drawn cards, choosing a card to discard. The
#: value is a dict carrying at least ``player``, who must equal ``to_play``. This is
#: the mechanism by which a decision can belong to the player whose turn it is *not*.
#: Games that need no sub-decisions simply omit the key.
PENDING = "pending"

#: Present on every action: a one-line human-readable rendering. The tactician sees
#: these strings and returns an index, so an action that reads ambiguously is a
#: decision-quality bug even when the mechanics are right.
LABEL = "label"

#: The keys the core owns and every state must carry.
CORE_KEYS: tuple[str, ...] = (TO_PLAY, TURN, PLY)


class Viewer(StrEnum):
    """Who is being shown the state.

    Not the same thing as a player: ``PUBLIC`` is the view of the table itself,
    which is what the CLI shows and what deduction memory may be built from.
    """

    PUBLIC = "public"
    AUTOMA = "automa"
    HUMAN = "human"


def viewer_player(viewer: Viewer) -> Player | None:
    """The player whose private information ``viewer`` may see, if any."""
    return {Viewer.AUTOMA: AUTOMA, Viewer.HUMAN: HUMAN}.get(viewer)


@runtime_checkable
class GameModule(Protocol):
    """What a game module must provide.

    ``apply`` must not mutate the state it is given. This is not a convention: the
    self-play harness snapshots the input and deep-compares it after every call, so
    a mutating implementation fails validation rather than corrupting a match
    silently.
    """

    NAME: str
    PLAYERS: tuple[Player, Player]

    @staticmethod
    def initial_state(seed: int | None = None) -> State:
        """A fresh state. Equal seeds must produce equal states.

        Seeding is what lets Phase 3 build reproducible fixtures, and it lets any
        shuffling happen once here rather than being threaded through ``apply`` -
        which is what keeps ``apply`` a pure function.
        """

    @staticmethod
    def legal_actions(state: State) -> list[Action]:
        """Every action available to ``state[TO_PLAY]``, in a stable order.

        Non-empty whenever the state is not terminal. This is the single method
        that makes invariant 1 true: the core enumerates, the model picks an index.
        """

    @staticmethod
    def apply(state: State, action: Action) -> State:
        """Return a NEW state with ``action`` applied. Never mutates ``state``."""

    @staticmethod
    def is_terminal(state: State) -> bool:
        """Whether the match is over."""

    @staticmethod
    def winner(state: State) -> Player | None:
        """The winner, or ``None`` for a draw. Raises ``ValueError`` off-terminal.

        ``None`` means draw and nothing else - overloading it with "not finished"
        would make an unfinished match indistinguishable from a drawn one at exactly
        the moment the distinction matters.
        """

    @staticmethod
    def describe_state(state: State, viewer: Viewer) -> str:
        """Render the state as text for one viewer, revealing nothing it may not see."""

    @staticmethod
    def check_invariants(state: State) -> list[str]:
        """OPTIONAL. Structural problems with this state, empty when sound.

        The core cannot know generically that a game conserves its components, so
        the module declares it. plan.md requires the harness to catch conservation
        violations; this is how it learns what conservation means for this game.
        """


#: name -> number of parameters the function must accept positionally.
_REQUIRED: dict[str, int] = {
    "initial_state": 1,
    "legal_actions": 1,
    "apply": 2,
    "is_terminal": 1,
    "winner": 1,
    "describe_state": 2,
}

_OPTIONAL: dict[str, int] = {"check_invariants": 1}


def _arity_problem(name: str, fn: Any, wanted: int) -> str | None:
    if not callable(fn):
        return f"{name} is not callable"
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):  # builtins and C functions have no signature
        return None
    positional = [
        p
        for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
    ]
    if any(p.kind is p.VAR_POSITIONAL for p in positional):
        return None
    required = sum(1 for p in positional if p.default is p.empty)
    if required > wanted or len(positional) < wanted:
        return f"{name} must accept {wanted} positional argument(s), got {len(positional)}"
    return None


def conforms(module: ModuleType) -> list[str]:
    """Reasons ``module`` fails the contract. Empty means it conforms.

    Returned as readable strings rather than raised, because Phase 4 feeds a
    generated module through here and wants every problem at once to put in the
    repair prompt - not the first one and a traceback.
    """
    problems: list[str] = []

    name = getattr(module, "NAME", None)
    if not isinstance(name, str) or not name:
        problems.append("NAME must be a non-empty string")

    players = getattr(module, "PLAYERS", None)
    if (
        not isinstance(players, tuple)
        or len(players) != 2
        or not all(isinstance(p, str) and p for p in players)
        or players[0] == players[1]
    ):
        problems.append("PLAYERS must be a tuple of two distinct non-empty strings")

    for fn_name, arity in _REQUIRED.items():
        fn = getattr(module, fn_name, None)
        if fn is None:
            problems.append(f"{fn_name} is missing")
            continue
        problem = _arity_problem(fn_name, fn, arity)
        if problem:
            problems.append(problem)

    for fn_name, arity in _OPTIONAL.items():
        fn = getattr(module, fn_name, None)
        if fn is not None:
            problem = _arity_problem(fn_name, fn, arity)
            if problem:
                problems.append(problem)

    return problems
