"""The fixed case battery: one decision each, with ground truth.

A match confounds two things - whether a player understands the rules, and whether it
got lucky. Phase 2 showed how badly: on Nim, Automa played a *losing* move, justified
it with a false statement about the board, and won the match anyway because the random
opponent blundered back. Single decisions from fixed positions separate those.

**Positions are reached, not constructed.** A case names a game, a seed and a ply;
replaying the seeded match with a seeded random driver lands on exactly that state
every time. Hand-building states would risk positions the game can never actually
reach, which would be a worse test than no test.

**Ground truth is derived from the module, not transcribed.** `dnup.py` and `nim.py`
were validated in Phase 1 by 500 random matches each plus hand-computed unit tests, so
the module is the most reliable statement of what is legal that this project has;
copying its answers into a fixture by hand could only introduce errors. Nim's
additionally carries an optimal-play answer from `nim.optimal_actions`, the nim-sum
oracle checked against brute-force search.

The categories `plan.md` names were written before either game existed, and two of
them - conditional card effects, resource accounting - describe mechanics neither game
has. Those are substituted rather than faked; see `CATEGORIES`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from automa.core.game import TO_PLAY, TURN, settle
from automa.core.loader import load_game

#: What each category is testing, and where it departs from `plan.md`.
CATEGORIES: dict[str, str] = {
    "obvious_legality": "A move that is plainly available, or plainly is not.",
    "subtle_legality": (
        "DNUP's displacement rule: a set may only be played into a size already on "
        "the table if it beats that set's value. The single most missable rule."
    ),
    "conditional_branch": (
        "Substituted for plan.md's 'conditional card effect' - neither game has card "
        "effects (they arrive in Phase 5). The conditional branches that do exist: a "
        "set displaced from your own other play area, and an add blocked by a rival."
    ),
    "orientation_accounting": (
        "Substituted for plan.md's 'resource accounting' - neither game has "
        "resources. DNUP's real bookkeeping burden is that a card taken from the "
        "table flips to its other value."
    ),
    "terminal": "A position one move from ending a round or the match.",
    "control": "A position where every action is legal and roughly equivalent.",
}


@dataclass(frozen=True)
class Case:
    """One fixed decision point."""

    case_id: str
    game: str
    seed: int
    ply: int
    category: str
    note: str = ""


@dataclass
class ResolvedCase:
    """A case with its state and ground truth attached."""

    case: Case
    state: dict[str, Any]
    legal: list[dict[str, Any]] = field(default_factory=list)
    optimal: list[dict[str, Any]] = field(default_factory=list)
    to_play: str = ""

    @property
    def has_optimal(self) -> bool:
        """Only Nim is solved, so only Nim can score decision quality exactly."""
        return bool(self.optimal)


#: 16 cases: 6 Nim, 10 DNUP. Every category in CATEGORIES is represented, and the
#: seed/ply pairs were chosen by inspecting the resolved positions (see
#: `tests/test_eval_cases.py`, which asserts each one really is what it claims).
BATTERY: tuple[Case, ...] = (
    # -- Nim: perfect information, so these isolate reasoning from bookkeeping.
    Case("nim-open", "nim", 1, 0, "control", "Opening position, everything legal."),
    Case("nim-obvious", "nim", 1, 2, "obvious_legality", "Mid-game; exactly one winning move."),
    Case("nim-endgame", "nim", 5, 4, "terminal", "Two objects in one heap; taking both wins."),
    Case("nim-last", "nim", 2, 3, "terminal", "Near the end of a different match."),
    Case("nim-mid", "nim", 5, 2, "obvious_legality", "Third seed, mid-game."),
    Case("nim-early", "nim", 7, 1, "control", "Fourth seed, early."),
    # -- DNUP: hidden hands, a contested table, and orientation to track.
    Case("dnup-open", "dnup", 1, 0, "control", "Opening deal, nothing on the table."),
    Case("dnup-early", "dnup", 1, 2, "obvious_legality", "Sets are on the table."),
    Case("dnup-contested", "dnup", 1, 5, "subtle_legality", "Displacement is in play."),
    Case("dnup-mid", "dnup", 1, 9, "orientation_accounting", "Cards have been taken and flipped."),
    Case("dnup-late", "dnup", 1, 20, "conditional_branch", "Both players hold sets."),
    Case("dnup-s2-early", "dnup", 2, 3, "obvious_legality", "Second seed."),
    Case("dnup-s2-mid", "dnup", 2, 11, "subtle_legality", "Second seed, contested."),
    Case("dnup-s3-mid", "dnup", 3, 8, "conditional_branch", "Third seed."),
    Case("dnup-s3-late", "dnup", 3, 17, "orientation_accounting", "Third seed, later."),
    Case("dnup-s4", "dnup", 4, 6, "obvious_legality", "Fourth seed."),
)


def resolve(case: Case) -> ResolvedCase:
    """Replay to the case's position and attach the ground truth.

    Deterministic in ``(seed, ply)``: the same case always yields the same state, which
    is what lets the baseline and the agent be asked precisely the same question.
    """
    game = load_game(case.game)
    state = settle(game, game.initial_state(case.seed))
    rng = random.Random(f"case-{case.seed}")

    for _ in range(case.ply):
        if game.is_terminal(state):
            raise ValueError(f"{case.case_id}: match ended before ply {case.ply}")
        actions = game.legal_actions(state)
        state = game.apply(state, rng.choice(actions))

    if game.is_terminal(state):
        raise ValueError(f"{case.case_id}: position is terminal, nothing to decide")

    legal = game.legal_actions(state)
    optimal = list(game.optimal_actions(state)) if hasattr(game, "optimal_actions") else []
    return ResolvedCase(
        case=case,
        state=state,
        legal=legal,
        optimal=optimal,
        to_play=state[TO_PLAY],
    )


def resolved_battery(game: str | None = None) -> list[ResolvedCase]:
    """The whole battery, or one game's share of it."""
    return [resolve(c) for c in BATTERY if game is None or c.game == game]


def describe(resolved: ResolvedCase) -> str:
    """A one-line summary, for the report."""
    c = resolved.case
    return (
        f"{c.case_id:16} {c.game:5} seed={c.seed} ply={c.ply} turn={resolved.state[TURN]} "
        f"{c.category:24} {len(resolved.legal):3} legal"
        + (f", {len(resolved.optimal)} optimal" if resolved.has_optimal else "")
    )
