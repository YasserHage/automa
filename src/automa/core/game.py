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

#: Optional. When present and not ``None``, the module is missing information that only
#: the physical table has - what a just-revealed card actually does, what was drawn, what
#: a die showed - and cannot proceed until someone supplies it. The value is a dict
#: carrying at least ``kind`` and ``prompt``.
#:
#: Deliberately distinct from :data:`PENDING`. A pending decision is a *choice* among
#: options the module can enumerate; an unknown is a *fact* the module does not have and
#: cannot derive. Supplying a fact is neither a decision nor a corrective intervention,
#: and counting it as either would corrupt the primary metric.
UNKNOWN = "unknown"

#: Marker on an answer to :data:`UNKNOWN` meaning the table could not supply the fact.
#:
#: Every source of information can fail: the person does not remember what was drawn,
#: cannot describe what a card does, or describes something the effect vocabulary cannot
#: express. So "unavailable" is not an error path - it is an ordinary answer, and a
#: module that only handles answers which arrive is a module that stalls at the table.
#: ``resolve_unknown`` must accept it, clear the ``unknown`` it declared, record that the
#: question was asked so nobody is asked twice, and carry on without the fact.
#:
#: It is also what makes such a game validatable offline: the self-play harness has no
#: person to ask, so it answers every question this way. A module that cannot proceed
#: without an answer cannot be self-played, and would reach the table unvalidated.
UNAVAILABLE = "unavailable"


# -- the two kinds of question a module may ask ----------------------------
#
# Both are facts the table has and the module cannot derive, and both travel the same
# way: the module sets :data:`UNKNOWN`, the core relays it to an informant, the answer
# comes back to ``resolve_unknown``. What differs is what is being asked for.

#: What a card *does*. Answered from the catalog where possible, from the person
#: otherwise, and structured into vocabulary operations before it reaches the module.
KIND_CARD_EFFECT = "card_effect"

#: *Which* components are where - the cards actually dealt to a hand, the card just
#: drawn, the face a die showed. This is what makes a match a game played with the
#: physical deck rather than a simulation of one: the module declares the group it is
#: missing and the person reads it off the table.
#:
#: The answer is a list of plain strings and nothing more. Nobody between the person and
#: the module interprets them - the informant counts them, the module gives them meaning
#: - which is what keeps the core and the informant generic across games nobody has
#: written yet.
KIND_COMPONENTS = "components"

#: On a :data:`KIND_COMPONENTS` question: which group is being asked about. Echoed back
#: in the answer, so a module with several outstanding questions can tell them apart.
SLOT = "slot"

#: On a :data:`KIND_COMPONENTS` question: how many components are expected. This is the
#: whole of the validation a supplied answer gets - the right *number* of cards, never
#: the right names. A name the module does not recognise is a card nobody has described
#: yet, which is a question, not an error.
COUNT = "count"

#: OPTIONAL on a :data:`KIND_COMPONENTS` question: component names the module knows,
#: shown to the person as a hint. Never a filter: rejecting a name that is not on the
#: list would break exactly the unfamiliar-game case this product exists for.
OPTIONS = "options"

#: On an answer to a :data:`KIND_COMPONENTS` question: the components, as strings.
COMPONENTS = "components"

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


#: Fields that describe the *question* rather than identify it: what was asked, how it
#: was phrased, how many things it wanted, what names it would recognise. An answer
#: echoes everything else back, so a module holding several questions can tell which one
#: it is being answered.
_QUESTION_FIELDS: frozenset[str] = frozenset({"kind", "prompt", COUNT, OPTIONS})


def identifying(unknown: dict[str, Any]) -> dict[str, Any]:
    """The fields an answer echoes back, whatever else it carries.

    Defined once so the two possible answers to one question - the fact, and "nobody
    could say" - are distinguishable by what they carry and never by which of them the
    module can recognise.
    """
    return {k: v for k, v in unknown.items() if k not in _QUESTION_FIELDS}


def unavailable_answer(unknown: dict[str, Any]) -> dict[str, Any]:
    """The answer to :data:`UNKNOWN` that is available for every question in every game.

    The question's own identifying fields are echoed back, so a module can tell which of
    several outstanding questions this answers, and every field that would carry a
    *value* is simply absent - a module reading one with ``.get()`` sees ``None``.

    Defined once here because three places need exactly this and must not drift: random
    self-play, which has nobody to ask; an unattended match; and a person at the table
    who cannot say. They are the same answer, and a module only has to handle it once.
    """
    return {**identifying(unknown), UNAVAILABLE: True}


#: How many questions one state may ask before something is looping. Mirrors
#: ``match.MAX_UNKNOWNS_PER_TURN``: answering one can legitimately reveal another - a
#: hand supplied at the table can contain two cards nobody has described - but not
#: endlessly.
MAX_UNKNOWNS = 8


def settle(module: ModuleType, state: State) -> State:
    """Answer every question ``state`` declares with :func:`unavailable_answer`.

    What a caller with nobody to ask does with a fresh state. Modules now ask for the
    components on the table from the deal onwards, so ``initial_state`` routinely returns
    a state that is *waiting*, and ``legal_actions`` must never be called on one of those.
    Every offline caller - the fingerprint, the baseline's action-shape probe, the eval
    battery - needs the same three lines, so they live here once.

    The match loop and the self-play harness do not use this: they resolve the same way
    but have their own logging and checking to do at each step.
    """
    for _ in range(MAX_UNKNOWNS):
        if not isinstance(state, dict):
            return state
        unknown = state.get(UNKNOWN)
        if not unknown:
            return state
        resolve = getattr(module, "resolve_unknown", None)
        if resolve is None:
            raise ValueError(
                f"{getattr(module, 'NAME', module)} set {UNKNOWN!r} but has no "
                "resolve_unknown, so an answer would have nowhere to go"
            )
        state = resolve(state, unavailable_answer(unknown))
    raise ValueError(
        f"{getattr(module, 'NAME', module)} still reports missing information after "
        f"{MAX_UNKNOWNS} answers; resolve_unknown is not clearing what it declared"
    )


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

        A game played with physical components does not deal them here: it declares a
        :data:`KIND_COMPONENTS` question and lets the table say what was dealt. The
        seeded shuffle is what it falls back to when the answer is :data:`UNAVAILABLE`,
        which is what keeps such a game playable offline and validatable by self-play.
        So the state this returns may legitimately be one that is still waiting, and
        callers must :func:`settle` it before asking for legal actions.
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

        A state carrying :data:`UNKNOWN` is **sound, not broken**: components that have
        not been supplied yet are missing on purpose. Self-play checks a fresh state
        before it answers anything, so a module that counts its deck unconditionally
        would report its own undealt opening as a violation.
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

#: Optional members. Deliberately *not* declared in the Protocol body: it is
#: ``runtime_checkable``, so ``isinstance`` demands every member it lists, and an
#: optional method there would make conformance fail for every game that does not need
#: it. Listing them here keeps them arity-checked when present and absent otherwise.
#:
#: ``resolve_unknown(state, answer) -> State`` folds an answer to :data:`UNKNOWN` back
#: into the state. The core obtains the answer - from a person, structured by an agent -
#: and the *module* applies it, which is what preserves invariant 2: a model produced
#: data, the module wrote the state. A game that never lacks information omits it.
#: The answer may carry :data:`UNAVAILABLE`, and handling that case is not optional.
_OPTIONAL: dict[str, int] = {"check_invariants": 1, "resolve_unknown": 2}


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


def action_shape(action: Action) -> tuple[tuple[str, str], ...]:
    """An action's key/type signature, ignoring :data:`LABEL`.

    Two consumers need to agree on what "the same kind of action" means and must not
    drift apart: the Phase 3 baseline uses it to tell a *malformed* answer (retry, a
    formatting problem) from an *illegal* one (an intervention, a rules problem), and
    the Phase 4 compiler uses it to check a generated module offers the same action
    vocabulary as the hand-written one. Defined once, here, beside the action type.
    """
    def type_name(value: Any) -> str:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, list):
            inner = {type_name(v) for v in value} or {"any"}
            return f"list[{'|'.join(sorted(inner))}]"
        return {int: "int", str: "str", float: "float"}.get(type(value), type(value).__name__)

    return tuple(sorted((k, type_name(v)) for k, v in action.items() if k != LABEL))


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
