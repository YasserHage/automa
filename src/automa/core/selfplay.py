"""Random self-play validation of a game module.

A module that type-checks can still be unsound: it can mutate the state it was
handed, wander into a position with no legal action, lose a card every twentieth
turn, or never terminate. None of that shows up in a hand-written unit test, because
the failure lives in the states nobody thought to write down.

So the harness plays the game at random and checks the structural properties on every
single step. Phase 1 uses it to prove the two hand-written modules sound. Phase 4
reuses it unchanged as the first of four gates a *generated* module must pass before
a person ever sees it - which is the reason it is written game-agnostically here
rather than as a test fixture.

Pure Python, no model anywhere: the choice among legal actions is `random.Random`.
"""

from __future__ import annotations

import copy
import json
import random
import statistics
import traceback
from dataclasses import asdict, dataclass, field
from types import ModuleType
from typing import Any

from automa.core.game import CORE_KEYS, LABEL, PENDING, PLY, TO_PLAY, TURN

#: Generous by design. A game whose rules impose no bound on match length - DNUP is
#: one - terminates almost surely under random play but has no proven ceiling, so
#: this is a real stopping rule and not only a guard against a bug. Matches that hit
#: it are reported as `aborted` rather than silently dropped.
DEFAULT_MAX_PLIES = 1000


@dataclass(frozen=True)
class Violation:
    """One structural property, broken once, with enough context to reproduce it."""

    seed: int
    ply: int
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[seed {self.seed} ply {self.ply}] {self.check}: {self.detail}"


@dataclass(frozen=True)
class PlayoutResult:
    seed: int
    plies: int
    turns: int
    outcome: str  # "terminal" | "aborted" | "error"
    winner: str | None
    violations: tuple[Violation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations and self.outcome == "terminal"


@dataclass
class ValidationReport:
    """What `automa-validate` prints and writes, and what the exit criterion reads."""

    game: str
    matches: int
    max_plies: int
    outcomes: dict[str, int] = field(default_factory=dict)
    winners: dict[str, int] = field(default_factory=dict)
    ply_min: int = 0
    ply_median: float = 0.0
    ply_max: int = 0
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """The Phase 1 exit criterion for one game.

        Zero violations *and* zero aborts. An abort is not a crash and not a broken
        invariant, but it is a match that never finished, so it cannot be counted as
        a match that completed.
        """
        return not self.violations and self.outcomes.get("terminal", 0) == self.matches

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data

    def summary(self) -> str:
        head = "PASS" if self.ok else "FAIL"
        parts = [
            f"{head}  {self.game}: {self.matches} matches, "
            f"plies min/median/max {self.ply_min}/{self.ply_median:g}/{self.ply_max}",
            f"      outcomes {self.outcomes}  winners {self.winners}",
        ]
        if self.violations:
            parts.append(f"      {len(self.violations)} violation(s):")
            parts += [f"        {v}" for v in self.violations[:10]]
            if len(self.violations) > 10:
                parts.append(f"        ... and {len(self.violations) - 10} more")
        return "\n".join(parts)


def _json_round_trips(state: Any) -> str | None:
    """Whether the state survives the trip through JSON unchanged.

    States are logged to trajectories and handed to models as text, so a state that
    does not round-trip is a state the project cannot record or show. Tuples are the
    usual culprit: they serialise happily and come back as lists.
    """
    try:
        encoded = json.dumps(state)
    except (TypeError, ValueError) as exc:
        return f"state is not JSON-serialisable: {exc}"
    if json.loads(encoded) != state:
        return "state does not survive a JSON round-trip unchanged (tuples become lists?)"
    return None


def _check_state(module: ModuleType, state: Any, record: Any, ply: int) -> None:
    """Every property a state must hold on its own, regardless of how it was reached."""
    if not isinstance(state, dict):
        record("state_is_a_dict", f"got {type(state).__name__}", ply)
        return

    problem = _json_round_trips(state)
    if problem:
        record("state_round_trips_through_json", problem, ply)

    for key in CORE_KEYS:
        if key not in state:
            record("core_keys_present", f"state is missing {key!r}", ply)

    to_play = state.get(TO_PLAY)
    if to_play not in module.PLAYERS:
        record(
            "to_play_is_a_known_player",
            f"{to_play!r} is not one of {module.PLAYERS}",
            ply,
        )

    pending = state.get(PENDING)
    if pending is not None:
        if not isinstance(pending, dict) or "player" not in pending:
            record(
                "pending_is_well_formed",
                f"pending must be a dict carrying 'player': {pending!r}",
                ply,
            )
        elif pending["player"] != to_play:
            record(
                "pending_belongs_to_the_player_to_play",
                f"pending is {pending['player']!r}'s decision but to_play is {to_play!r}",
                ply,
            )

    check = getattr(module, "check_invariants", None)
    if check is not None:
        for problem in check(state):
            record("check_invariants", problem, ply)


def random_playout(
    module: ModuleType,
    seed: int,
    max_plies: int = DEFAULT_MAX_PLIES,
) -> PlayoutResult:
    """Play one match at random, checking every structural property at every step."""
    rng = random.Random(seed)
    violations: list[Violation] = []

    def record(check: str, detail: str, ply: int) -> None:
        violations.append(Violation(seed=seed, ply=ply, check=check, detail=detail))

    ply = 0
    state: Any = None
    outcome = "terminal"
    winner: str | None = None

    try:
        state = module.initial_state(seed)
        _check_state(module, state, record, ply)

        while ply < max_plies:
            if module.is_terminal(state):
                break

            actions = module.legal_actions(state)
            if not isinstance(actions, list) or not actions:
                record(
                    "legal_actions_non_empty_off_terminal",
                    f"no legal action for {state.get(TO_PLAY)!r} in a non-terminal state",
                    ply,
                )
                outcome = "error"
                break
            missing_label = [a for a in actions if not isinstance(a, dict) or LABEL not in a]
            if missing_label:
                record(
                    "actions_carry_a_label",
                    f"{len(missing_label)} action(s) without a {LABEL!r} key",
                    ply,
                )

            action = rng.choice(actions)
            before = copy.deepcopy(state)
            was_pending = state.get(PENDING) is not None

            new_state = module.apply(state, action)

            if state != before:
                record(
                    "apply_does_not_mutate_its_input",
                    f"applying {action.get(LABEL)!r} changed the state it was given",
                    ply,
                )
            _check_state(module, new_state, record, ply + 1)

            if isinstance(new_state, dict):
                if not _advanced(new_state.get(PLY), before.get(PLY)):
                    record(
                        "ply_strictly_increases",
                        f"{PLY} went {before.get(PLY)!r} -> {new_state.get(PLY)!r}",
                        ply + 1,
                    )
                if not _monotone(new_state.get(TURN), before.get(TURN)):
                    record(
                        "turn_is_non_decreasing",
                        f"{TURN} went {before.get(TURN)!r} -> {new_state.get(TURN)!r}",
                        ply + 1,
                    )
                elif was_pending and new_state.get(TURN) != before.get(TURN):
                    record(
                        "resolving_a_pending_decision_does_not_advance_the_turn",
                        f"{TURN} went {before.get(TURN)!r} -> {new_state.get(TURN)!r} "
                        "while resolving a sub-decision",
                        ply + 1,
                    )

            state = new_state
            ply += 1
        else:
            outcome = "aborted"

        if outcome == "terminal":
            if not module.is_terminal(state):
                outcome = "aborted"
            else:
                winner = module.winner(state)
                if winner is not None and winner not in module.PLAYERS:
                    record(
                        "winner_is_a_known_player_or_none",
                        f"{winner!r} is not one of {module.PLAYERS} and is not None",
                        ply,
                    )
    except Exception as exc:  # noqa: BLE001 - a module crash IS the finding
        record("no_exception_during_play", f"{type(exc).__name__}: {exc}", ply)
        record("no_exception_during_play_traceback", traceback.format_exc().strip(), ply)
        outcome = "error"

    turns = state.get(TURN, 0) if isinstance(state, dict) else 0
    return PlayoutResult(
        seed=seed,
        plies=ply,
        turns=turns if isinstance(turns, int) else 0,
        outcome=outcome,
        winner=winner,
        violations=tuple(violations),
    )


def _advanced(new: Any, old: Any) -> bool:
    return isinstance(new, int) and isinstance(old, int) and new > old


def _monotone(new: Any, old: Any) -> bool:
    return isinstance(new, int) and isinstance(old, int) and new >= old


def validate_module(
    module: ModuleType,
    matches: int = 500,
    seed0: int = 0,
    max_plies: int = DEFAULT_MAX_PLIES,
    max_violations: int = 200,
) -> ValidationReport:
    """Play ``matches`` random matches and aggregate what happened.

    Every violation carries the seed that produced it, so any failure the 500-match
    run finds is replayable with a single :func:`random_playout` call rather than by
    re-running the batch and hoping.

    The winner and length distributions are reported for a reason beyond curiosity:
    a game that is sound but degenerate - one player winning every match, or every
    match ending in three plies - is a bad evaluation substrate, and Phase 3 is
    downstream of this. Better to see it here.
    """
    report = ValidationReport(game=module.NAME, matches=matches, max_plies=max_plies)
    lengths: list[int] = []

    for i in range(matches):
        result = random_playout(module, seed=seed0 + i, max_plies=max_plies)
        report.outcomes[result.outcome] = report.outcomes.get(result.outcome, 0) + 1
        key = result.winner if result.winner is not None else ("draw" if result.ok else "-")
        report.winners[key] = report.winners.get(key, 0) + 1
        lengths.append(result.plies)
        for violation in result.violations:
            if len(report.violations) < max_violations:
                report.violations.append(violation)

    if lengths:
        report.ply_min = min(lengths)
        report.ply_median = statistics.median(lengths)
        report.ply_max = max(lengths)
    return report
