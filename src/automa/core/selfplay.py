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

from automa.core.game import (
    CORE_KEYS,
    LABEL,
    PENDING,
    PLY,
    TO_PLAY,
    TURN,
    UNKNOWN,
    Viewer,
    unavailable_answer,
)

#: Generous by design. A game whose rules impose no bound on match length - DNUP is
#: one - terminates almost surely under random play but has no proven ceiling, so
#: this is a real stopping rule and not only a guard against a bug. Matches that hit
#: it are reported as `aborted` rather than silently dropped.
DEFAULT_MAX_PLIES = 1000


class _Stalled(Exception):
    """A module that cannot take another step. Internal: the violation is already logged."""


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
    #: How many times the module declared missing information during this match.
    unknowns: int = 0

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
    #: Questions the module asked across every match. Evidence for the Phase 5 claim
    #: that a populated catalog asks fewer than an empty one, and a signal in its own
    #: right: a game that declares no unknowns is not exercising that path at all.
    unknowns: int = 0
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
        if self.unknowns:
            parts.append(f"      {self.unknowns} question(s) asked, all answered 'unavailable'")
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


def _check_views(module: ModuleType, state: Any, record: Any, ply: int) -> None:
    """Every viewer must get readable text, from every reachable state.

    This exists because Phase 4 found the gap the hard way. The harness exercised five
    of the six contract methods and left out `describe_state` - so a compiled module
    that raised `AttributeError` the moment anyone looked at the board passed 200
    matches of self-play with zero violations. `describe_state` is the method the whole
    human interface rests on, and it was the only one nothing checked.

    Not a privacy check: what is secret is game-specific and the core cannot know it.
    `tests/test_views.py` owns that. This only asserts the method works at all.
    """
    for viewer in Viewer:
        try:
            text = module.describe_state(state, viewer)
        except Exception as exc:
            record(
                "describe_state_renders_for_every_viewer",
                f"{viewer.value}: {type(exc).__name__}: {exc}",
                ply,
            )
            continue
        if not isinstance(text, str) or not text.strip():
            record(
                "describe_state_renders_for_every_viewer",
                f"{viewer.value}: returned {type(text).__name__}, expected non-empty text",
                ply,
            )


#: How many unknowns one step may declare before the harness calls it a loop. Mirrors
#: ``match.MAX_UNKNOWNS_PER_TURN``: answering one question can legitimately reveal
#: another, but not forever.
MAX_UNKNOWNS_PER_STEP = 8

#: Below this fraction of a module's own declared component vocabulary ever surfacing
#: as a card_effect question, self-play fails outright rather than merely reporting a
#: low number. With a reshuffled deck and matches in the hundreds, each starting from
#: an empty catalog, a module that checks for an unknown effect everywhere a card can
#: enter a scoring area should see nearly every declared type asked about within a
#: handful of matches. A gap this wide is the signature of a check that runs only at a
#: choke point - deal time, round start - that most played cards never pass through: the
#: first compiled Sushi Go module asked about exactly one of twelve declared card
#: types ("pudding", the only one this design happens to carry past a round boundary)
#: because its check ran after `score_round` had already read an empty catalog entry
#: for everything else and swept it into the discard pile. See Phase 5.
CARD_EFFECT_COVERAGE_MIN = 0.5

#: Below this many matches, a coverage gap could be sampling noise - a rare card type
#: might simply not have been dealt yet - rather than a real defect.
MIN_MATCHES_FOR_COVERAGE_CHECK = 50

#: Ordinary self-play answers every question "unavailable" (see the docstring on
#: `_resolve_unknowns`), which is correct for checking structural soundness but means a
#: catalog-driven module's scoring code - whatever reads `catalog["effects"]` and sums
#: it - is never actually exercised with a real number. A module can pass every other
#: gate while its scoring never fires and nothing would notice: exactly what happened
#: to the first compiled Sushi Go module, whose card_effect check ran after
#: `score_round` had already read an empty effects dict for every card and reset the
#: tableau - a live match reached a winner only through the pudding-count tiebreak
#: while `scores` stayed 0-0 for the entire game (docs/phase5_results.md, "the module
#: bug"). Comparing outcomes to a draw rate does not reliably catch this: a secondary
#: tiebreak the real scoring math never touches can keep producing plausible-looking
#: winner variance even while every point total is stuck at zero, as it did there. So
#: this check is *differential*, not a threshold on one number: for the same seed, it
#: replays a match twice - once the ordinary way (every question answered
#: "unavailable"), once with every "card_effect" question answered for real - and
#: compares the two winners. If a module's scoring genuinely reads what the catalog
#: records, feeding it real, varying points should change the outcome relative to the
#: no-information baseline on a good fraction of seeds; if the two are the same too
#: often, the recorded effects are not reaching the decision at all, whatever the
#: draw rate looks like on its own.
#:
#: The synthetic value varies *by card type*, not a flat constant, because a flat
#: constant leaked exactly the same failure mode it was meant to catch: two players who
#: play the same number of cards per round score identically under a flat value
#: regardless of which specific cards they held, so a flat effect agreed with the
#: no-information baseline on card-count parity alone (measured 57/60 times against a
#: known-good module - not a usable signal). Varying the point value by card type
#: instead ties the synthetic score to *which* cards a hand collected, decoupling it
#: from raw card count and separating a working module (52/100 agreement, measured)
#: from a broken one (100/100, measured) by a wide margin.
def _synthetic_effect_for(card_type: str) -> dict[str, Any]:
    points = 1 + (sum(ord(c) for c in card_type) % 9)
    return {"op": "score_flat", "points": points}


#: How many of the main run's seeds to replay for the scoring-differentiation check.
#: Deliberately smaller than the main self-play run - this is a coarse sanity signal on
#: top of it, not another exhaustive structural sweep.
SCORING_CHECK_MATCHES = 100

#: Below this many countable pairs (both replays reaching a terminal state), the sample
#: is too small to draw a conclusion from and the check is skipped rather than risking
#: a false failure.
MIN_MATCHES_FOR_SCORING_CHECK = 30

#: At or above this fraction of seeds where the informed winner matches the baseline
#: winner exactly, scoring is not influencing the outcome. Measured 52% agreement
#: against a module whose scoring genuinely reads recorded effects and 100% against one
#: whose points-adding code was deliberately deleted while its (unrelated) pudding
#: tiebreak was left intact - a wide enough gap to threshold well below the broken
#: case's 100% without crowding a real module's expected disagreement rate.
SCORING_AGREEMENT_MAX = 0.9


def _resolve_unknowns(
    module: ModuleType,
    state: Any,
    record: Any,
    ply: int,
    component_options: set[str],
    card_effect_types: set[str],
) -> tuple[Any, int, bool]:
    """Close every gap the module declares, as ``match.play_match`` does before a turn.

    Without this a module that correctly declares missing information deadlocks under
    self-play: ``legal_actions`` is asked for moves in a state the module has said it
    cannot proceed from. The first compiled Sushi Go modules invented a no-op "wait"
    action to escape that, which then could not advance ``ply`` - a defect the harness
    caused rather than found.

    ``component_options`` and ``card_effect_types`` are accumulated across the whole
    validation run (every match, not just this one): every name a "components" question
    ever offered as ``options``, and every card type a "card_effect" question ever named.
    Comparing the two afterwards is what catches a module that only checks for an
    unknown effect at deal time or round start - a card played and discarded mid-round
    never shows up in ``card_effect_types`` even though the module itself declared it as
    one of the names a person could be asked to supply.
    """
    asked = 0
    for _ in range(MAX_UNKNOWNS_PER_STEP):
        if not isinstance(state, dict):
            return state, asked, True
        unknown = state.get(UNKNOWN)
        if not unknown:
            return state, asked, True
        if not isinstance(unknown, dict) or "prompt" not in unknown or "kind" not in unknown:
            record(
                "unknown_is_well_formed",
                f"{UNKNOWN!r} must be a dict carrying 'kind' and 'prompt': {unknown!r}",
                ply,
            )
            return state, asked, False

        kind = unknown.get("kind")
        if kind == "components":
            options = unknown.get("options")
            if isinstance(options, list):
                component_options.update(str(o) for o in options)
        elif kind == "card_effect":
            card_type = unknown.get("card_type")
            if card_type:
                card_effect_types.add(str(card_type))

        resolve = getattr(module, "resolve_unknown", None)
        if resolve is None:
            record(
                "declaring_an_unknown_requires_resolve_unknown",
                f"state set {UNKNOWN!r} ({unknown.get('kind')}) but the module has no "
                "resolve_unknown, so an answer would have nowhere to go",
                ply,
            )
            return state, asked, False

        before = state
        state = resolve(state, unavailable_answer(unknown))
        asked += 1
        _check_state(module, state, record, ply)
        if isinstance(state, dict) and isinstance(before, dict):
            for key in (PLY, TURN):
                if not _monotone(state.get(key), before.get(key)):
                    record(
                        "resolving_an_unknown_does_not_rewind_the_clock",
                        f"{key} went {before.get(key)!r} -> {state.get(key)!r} while "
                        "answering a question",
                        ply,
                    )

    record(
        "resolve_unknown_clears_what_it_answers",
        f"still missing information after {MAX_UNKNOWNS_PER_STEP} answers on one step",
        ply,
    )
    return state, asked, False


#: How often to render the state during a playout. Every ply would triple the cost of
#: validation to re-check something that rarely varies; never would have missed the bug
#: that put this here. Periodic catches breakage that only appears once the board fills.
VIEW_CHECK_EVERY = 25


def random_playout(
    module: ModuleType,
    seed: int,
    max_plies: int = DEFAULT_MAX_PLIES,
    component_options: set[str] | None = None,
    card_effect_types: set[str] | None = None,
) -> PlayoutResult:
    """Play one match at random, checking every structural property at every step.

    ``component_options`` and ``card_effect_types`` accumulate across a whole
    :func:`validate_module` run when the caller passes the same sets into every match;
    a standalone call (as from a test exercising one seed) can leave them as ``None``
    and get fresh, throwaway sets.
    """
    rng = random.Random(seed)
    violations: list[Violation] = []
    if component_options is None:
        component_options = set()
    if card_effect_types is None:
        card_effect_types = set()

    def record(check: str, detail: str, ply: int) -> None:
        violations.append(Violation(seed=seed, ply=ply, check=check, detail=detail))

    ply = 0
    state: Any = None
    outcome = "terminal"
    winner: str | None = None
    unknowns = 0

    try:
        state = module.initial_state(seed)
        _check_state(module, state, record, ply)
        # Before the views: a module may legitimately be missing information from the
        # deal onwards, and describe_state has to render that state too.
        state, asked, proceed = _resolve_unknowns(
            module, state, record, ply, component_options, card_effect_types
        )
        unknowns += asked
        _check_views(module, state, record, ply)
        if not proceed:
            raise _Stalled

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
            # Answering happens between actions, exactly where play_match does it, so
            # legal_actions is never asked for moves in a state the module has said it
            # cannot proceed from.
            state, asked, proceed = _resolve_unknowns(
                module, state, record, ply, component_options, card_effect_types
            )
            unknowns += asked
            if ply % VIEW_CHECK_EVERY == 0:
                _check_views(module, state, record, ply)
            if not proceed:
                raise _Stalled
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
    except _Stalled:
        # The violation is already recorded and reproducible; there is nothing further
        # to learn from a match that cannot take another step.
        outcome = "error"
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
        unknowns=unknowns,
    )


def _advanced(new: Any, old: Any) -> bool:
    return isinstance(new, int) and isinstance(old, int) and new > old


def _monotone(new: Any, old: Any) -> bool:
    return isinstance(new, int) and isinstance(old, int) and new >= old


def _resolve_unknowns_replay(module: ModuleType, state: Any, informed: bool) -> tuple[Any, bool]:
    """Like `_resolve_unknowns`, but for the scoring-differentiation check below.

    With ``informed=False`` this is the ordinary self-play convention - every question
    answered "unavailable" - so it produces the same baseline a module is validated
    against everywhere else. With ``informed=True``, "card_effect" questions get a real,
    per-card-type synthetic effect instead. No violations are recorded here and no
    structural properties are checked: a crash or a stall is reported to the caller as
    "inconclusive" and excluded from the tally, not treated as a self-play failure in
    its own right - a synthetic effect is not a claim about correctness the way random
    self-play's checks are, only a probe for whether scoring reacts to it at all.
    """
    for _ in range(MAX_UNKNOWNS_PER_STEP):
        if not isinstance(state, dict):
            return state, True
        unknown = state.get(UNKNOWN)
        if not unknown:
            return state, True
        if not isinstance(unknown, dict) or "kind" not in unknown:
            return state, False
        resolve = getattr(module, "resolve_unknown", None)
        if resolve is None:
            return state, False
        if informed and unknown.get("kind") == "card_effect":
            card_type = unknown.get("card_type")
            answer = {"card_type": card_type, "effects": [_synthetic_effect_for(card_type)]}
        else:
            answer = unavailable_answer(unknown)
        state = resolve(state, answer)
    return state, False


def _replay_outcome(
    module: ModuleType, seed: int, informed: bool, max_plies: int
) -> str | None:
    """One playout, seeded identically to the main self-play run for this seed.

    Returns "automa"/"human"/"draw", or ``None`` if the match could not be played out
    to a countable result - excluded from the tally rather than counted as a draw, so a
    crash cannot masquerade as one. With the same seed and the same sequence of random
    choices, an ``informed=True`` and an ``informed=False`` run diverge only in what
    `resolve_unknown` is told about a card's effect - so a difference in the two
    outcomes can only come from scoring actually reacting to that.
    """
    try:
        rng = random.Random(seed)
        state = module.initial_state(seed)
        state, proceed = _resolve_unknowns_replay(module, state, informed)
        if not proceed:
            return None

        ply = 0
        while ply < max_plies:
            if module.is_terminal(state):
                break
            actions = module.legal_actions(state)
            if not isinstance(actions, list) or not actions:
                return None
            state = module.apply(state, rng.choice(actions))
            ply += 1
            state, proceed = _resolve_unknowns_replay(module, state, informed)
            if not proceed:
                return None
        else:
            return None

        if not module.is_terminal(state):
            return None
        winner = module.winner(state)
        return winner if winner is not None else "draw"
    except Exception:
        return None


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
    component_options: set[str] = set()
    card_effect_types: set[str] = set()

    for i in range(matches):
        result = random_playout(
            module,
            seed=seed0 + i,
            max_plies=max_plies,
            component_options=component_options,
            card_effect_types=card_effect_types,
        )
        report.outcomes[result.outcome] = report.outcomes.get(result.outcome, 0) + 1
        key = result.winner if result.winner is not None else ("draw" if result.ok else "-")
        report.winners[key] = report.winners.get(key, 0) + 1
        lengths.append(result.plies)
        report.unknowns += result.unknowns
        for violation in result.violations:
            if len(report.violations) < max_violations:
                report.violations.append(violation)

    if lengths:
        report.ply_min = min(lengths)
        report.ply_median = statistics.median(lengths)
        report.ply_max = max(lengths)

    if matches >= MIN_MATCHES_FOR_COVERAGE_CHECK and component_options:
        covered = component_options & card_effect_types
        if len(covered) < len(component_options) * CARD_EFFECT_COVERAGE_MIN:
            missing = sorted(component_options - card_effect_types)
            report.violations.append(
                Violation(
                    seed=-1,
                    ply=-1,
                    check="card_effect_covers_the_declared_component_vocabulary",
                    detail=(
                        f"(aggregate over all {matches} matches) only "
                        f"{len(covered)}/{len(component_options)} component types the "
                        "module itself offered as an 'options' list ever came back as "
                        f"the card_type of a card_effect question - missing, e.g., "
                        f"{missing[:10]}. A catalog-driven module must check for an "
                        "unknown effect at the moment a card enters a scoring area, not "
                        "only when a hand is dealt or a round starts: a card type this "
                        "sparse in card_effect questions is being asked about, if at "
                        "all, only at a choke point that most played cards never pass "
                        "through - so scoring silently reads an empty catalog entry for "
                        "them instead."
                    ),
                )
            )

    supported = getattr(module, "SUPPORTED_OPS", None)
    if supported and "score_flat" in supported and matches >= MIN_MATCHES_FOR_SCORING_CHECK:
        agree = 0
        counted = 0
        for i in range(min(matches, SCORING_CHECK_MATCHES)):
            seed = seed0 + i
            baseline = _replay_outcome(module, seed, informed=False, max_plies=max_plies)
            informed = _replay_outcome(module, seed, informed=True, max_plies=max_plies)
            if baseline is None or informed is None:
                continue
            counted += 1
            if baseline == informed:
                agree += 1

        if counted >= MIN_MATCHES_FOR_SCORING_CHECK and agree >= counted * SCORING_AGREEMENT_MAX:
            report.violations.append(
                Violation(
                    seed=-1,
                    ply=-1,
                    check="scoring_reacts_to_a_cards_recorded_effect",
                    detail=(
                        f"(aggregate over {counted} matches, each replayed twice from "
                        "the same seed) the winner was identical "
                        f"{agree}/{counted} times whether every card_effect question "
                        "was answered the ordinary self-play way ('unavailable') or "
                        "with a real, per-card-type point value. If scoring actually "
                        "read what gets recorded for a card, giving it real points "
                        "instead of none should change the outcome on more than a "
                        "sliver of seeds. Agreement this high means whatever sums "
                        "points is not reading the catalog at all, or is reading it "
                        "after the state holding the card has already been reset or "
                        "discarded - the exact way the first compiled Sushi Go module "
                        "stayed 0-0 for an entire live match while a pudding-count "
                        "tiebreak alone still picked a winner."
                    ),
                )
            )
    return report
