"""Nim - the deliberately un-card-like game.

Several heaps of objects. On your turn you take any positive number of objects from
exactly one heap. Taking the last object wins.

Nim is here precisely because it is *not* a card game: no hand, no deck, no hidden
information, no card effects. Pairing it with DNUP is what stops the interface from
quietly assuming a shape that only card games have - which is the whole reason
plan.md asks for two structurally distant games before the compiler exists.

Ancient and public domain, so invariant 6 is satisfied by construction.

:func:`optimal_actions` is **outside** the contract and never consulted during play.
It exists because Nim is solved: it gives Phase 3 and Phase 6 a perfect-play oracle,
which turns decision *quality* into something measurable rather than only legality.
"""

from __future__ import annotations

import random
from functools import reduce
from operator import xor

from automa.core.game import (
    DEFAULT_PLAYERS,
    LABEL,
    PLY,
    TO_PLAY,
    TURN,
    Action,
    Player,
    State,
    Viewer,
)

NAME = "nim"
PLAYERS = DEFAULT_PLAYERS

#: Heap sizes when no seed is given. The classic starting position.
DEFAULT_HEAPS = (3, 5, 7)

#: Seeded games draw heaps from this range. Small enough that a random match ends in
#: a handful of turns, varied enough that 500 seeds cover genuinely different
#: positions rather than replaying one board.
_HEAP_COUNT = 3
_HEAP_MAX = 7

_HEAP_NAMES = "ABCDEFGH"


def initial_state(seed: int | None = None) -> State:
    """A fresh position. Equal seeds give equal states.

    With a seed, both the heaps and the starting player are drawn - the starter
    especially, because always opening with the same player would bake a first-move
    advantage into every winner distribution the validator reports.
    """
    if seed is None:
        heaps = list(DEFAULT_HEAPS)
        to_play: Player = PLAYERS[0]
    else:
        rng = random.Random(f"nim-{seed}")
        heaps = [rng.randint(1, _HEAP_MAX) for _ in range(_HEAP_COUNT)]
        to_play = rng.choice(PLAYERS)

    return {
        TO_PLAY: to_play,
        TURN: 0,
        PLY: 0,
        "heaps": heaps,
        "last_mover": None,
    }


def legal_actions(state: State) -> list[Action]:
    """Every (heap, count) pair available. Empty only when the position is terminal."""
    actions: list[Action] = []
    for index, size in enumerate(state["heaps"]):
        for count in range(1, size + 1):
            actions.append(
                {
                    LABEL: f"Take {count} from heap {_HEAP_NAMES[index]} (has {size})",
                    "heap": index,
                    "count": count,
                }
            )
    return actions


def apply(state: State, action: Action) -> State:
    """Remove ``count`` objects from one heap and pass the turn."""
    index, count = action["heap"], action["count"]
    heaps = list(state["heaps"])

    if not 0 <= index < len(heaps):
        raise ValueError(f"no heap {index}; there are {len(heaps)}")
    if not 1 <= count <= heaps[index]:
        raise ValueError(f"cannot take {count} from heap {index} of size {heaps[index]}")

    heaps[index] -= count
    mover = state[TO_PLAY]
    return {
        **state,
        TO_PLAY: _other(mover),
        TURN: state[TURN] + 1,
        PLY: state[PLY] + 1,
        "heaps": heaps,
        "last_mover": mover,
    }


def is_terminal(state: State) -> bool:
    return not any(state["heaps"])


def winner(state: State) -> Player | None:
    """Whoever took the last object. Never a draw - Nim cannot end level."""
    if not is_terminal(state):
        raise ValueError("winner() is only defined on a terminal state")
    return state["last_mover"]


def describe_state(state: State, viewer: Viewer) -> str:
    """Nim is a game of perfect information, so every viewer sees the same thing."""
    del viewer  # no hidden information exists in Nim
    heaps = ", ".join(
        f"{_HEAP_NAMES[i]}: {'o' * size if size else '(empty)'} ({size})"
        for i, size in enumerate(state["heaps"])
    )
    return (
        f"Nim - turn {state[TURN]}, {state[TO_PLAY]} to play.\n"
        f"Heaps: {heaps}\n"
        f"Take any number of objects from a single heap. Taking the last object wins."
    )


def check_invariants(state: State) -> list[str]:
    problems: list[str] = []
    heaps = state["heaps"]
    if not isinstance(heaps, list) or not heaps:
        problems.append(f"heaps must be a non-empty list, got {heaps!r}")
        return problems
    for i, size in enumerate(heaps):
        if not isinstance(size, int) or size < 0:
            problems.append(f"heap {i} is {size!r}; heap sizes are non-negative integers")
    if is_terminal(state) and state["last_mover"] is None:
        problems.append("the position is terminal but nobody has moved")
    return problems


# -- outside the contract --------------------------------------------------


def optimal_actions(state: State) -> list[Action]:
    """Perfect-play moves from this position, by the nim-sum rule.

    A position is lost for the player to move exactly when the XOR of the heap sizes
    is zero. From a non-zero position, a winning move takes one heap down to
    ``size ^ nim_sum``; from a zero position every move loses, so every legal action
    is returned and the caller learns the position itself was already lost.

    Never called during a match. This is scoring apparatus for Phase 3 and Phase 6:
    with it, "did the tactician play well?" is a question with an exact answer.
    """
    actions = legal_actions(state)
    nim_sum = reduce(xor, state["heaps"], 0)
    if nim_sum == 0:
        return actions
    best = []
    for action in actions:
        size = state["heaps"][action["heap"]]
        if size - action["count"] == size ^ nim_sum:
            best.append(action)
    return best


def _other(player: Player) -> Player:
    return PLAYERS[1] if player == PLAYERS[0] else PLAYERS[0]
