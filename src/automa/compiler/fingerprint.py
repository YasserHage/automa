"""Comparing two game modules that share no internal representation.

`plan.md` asks the Compiler to produce a module "functionally equivalent" to the
hand-written one. Taken literally that cannot be checked: two modules can implement the
same game with entirely different state schemas, so there is no position-by-position
comparison to make. Asking the generated module to adopt `dnup.py`'s schema would make
the check easy and the result meaningless - it would show a model can fill in a template,
not that it can compile rules.

What *can* be compared is behaviour through the interface, which both modules share by
construction. A fingerprint is a handful of cheap, schema-independent measurements taken
from seeded random self-play: which action kinds exist, what shape they have, how many
components the game conserves, how long matches run, how the branching factor behaves.

**This is evidence, not proof.** Two genuinely different games could produce matching
statistics. It is one gate among several, and the report prints both fingerprints side by
side so a person reads the comparison rather than a pass/fail token. It is also model-free
and takes seconds, which is what lets a failed generation cost one cheap cycle.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass, field
from types import ModuleType
from typing import Any

from automa.core.game import LABEL, TO_PLAY, action_shape


@dataclass
class Fingerprint:
    """How a module behaves, measured rather than declared."""

    game: str
    players: list[str] = field(default_factory=list)
    action_kinds: list[str] = field(default_factory=list)
    action_shapes: list[str] = field(default_factory=list)
    component_count: int | None = None
    actions_per_turn_median: float = 0.0
    actions_per_turn_max: int = 0
    match_len_min: int = 0
    match_len_median: float = 0.0
    match_len_max: int = 0
    winner_balance: dict[str, int] = field(default_factory=dict)
    terminal_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Keys whose value looks like a category rather than a quantity. `kind` is the
#: convention both hand-written games use; anything else is ignored rather than guessed.
_KIND_KEYS = ("kind", "type", "action")


def _kinds(action: dict[str, Any]) -> str | None:
    for key in _KIND_KEYS:
        value = action.get(key)
        if isinstance(value, str):
            return value
    return None


def _component_count(state: Any) -> int | None:
    """Total cards/pieces the state holds, if the state is shaped like a card game.

    Counts every list of dicts carrying an ``id``, which is how both hand-written
    modules represent components. A module that represents them otherwise simply
    reports ``None`` rather than a wrong number - an absent measurement is honest, a
    fabricated one is not.
    """
    if not isinstance(state, dict):
        return None
    total = 0
    found = False

    def walk(value: Any) -> None:
        nonlocal total, found
        if isinstance(value, list):
            if value and all(isinstance(v, dict) and "id" in v for v in value):
                total += len(value)
                found = True
            else:
                for item in value:
                    walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(state)
    return total if found else None


def collect(module: ModuleType, matches: int = 40, max_plies: int = 1000) -> Fingerprint:
    """Measure a module by playing it at random. No model involved."""
    fp = Fingerprint(game=getattr(module, "NAME", "?"), players=list(module.PLAYERS))
    kinds: set[str] = set()
    shapes: set[str] = set()
    counts: list[int] = []
    lengths: list[int] = []
    winners: dict[str, int] = {}
    terminal = 0

    for seed in range(matches):
        rng = random.Random(f"fingerprint-{seed}")
        state = module.initial_state(seed)
        if fp.component_count is None:
            fp.component_count = _component_count(state)

        ply = 0
        while ply < max_plies and not module.is_terminal(state):
            actions = module.legal_actions(state)
            if not actions:
                break
            counts.append(len(actions))
            for action in actions:
                kind = _kinds(action)
                if kind:
                    kinds.add(kind)
                shapes.add(",".join(f"{k}:{t}" for k, t in action_shape(action)))
            state = module.apply(state, rng.choice(actions))
            ply += 1

        lengths.append(ply)
        if module.is_terminal(state):
            terminal += 1
            winner = module.winner(state)
            key = winner if winner is not None else "draw"
            winners[key] = winners.get(key, 0) + 1

    fp.action_kinds = sorted(kinds)
    fp.action_shapes = sorted(shapes)
    fp.actions_per_turn_median = statistics.median(counts) if counts else 0
    fp.actions_per_turn_max = max(counts) if counts else 0
    fp.match_len_min = min(lengths) if lengths else 0
    fp.match_len_median = statistics.median(lengths) if lengths else 0
    fp.match_len_max = max(lengths) if lengths else 0
    fp.winner_balance = winners
    fp.terminal_rate = round(terminal / max(matches, 1), 3)
    return fp


@dataclass
class Comparison:
    """A reference fingerprint against a candidate's, difference by difference."""

    matches: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences


def compare(reference: Fingerprint, candidate: Fingerprint, tolerance: float = 0.5) -> Comparison:
    """Compare two fingerprints.

    Exact agreement is required on the things a different game would get *wrong*:
    the action vocabulary, the action shapes, the component count, the player count.
    Distributions are compared within a wide tolerance, because random self-play is
    noisy and a module can be correct while playing out somewhat differently - only a
    gross divergence (an order of magnitude) means a different game.
    """
    result = Comparison()

    def check(name: str, ref: Any, cand: Any) -> None:
        if ref == cand:
            result.matches.append(f"{name}: {ref}")
        else:
            result.differences.append(f"{name}: expected {ref!r}, got {cand!r}")

    check("action kinds", reference.action_kinds, candidate.action_kinds)
    check("action shapes", reference.action_shapes, candidate.action_shapes)
    check("component count", reference.component_count, candidate.component_count)
    check("players", len(reference.players), len(candidate.players))
    check("every match terminates", reference.terminal_rate, candidate.terminal_rate)

    for name, ref, cand in (
        ("actions per turn (median)", reference.actions_per_turn_median,
         candidate.actions_per_turn_median),
        ("match length (median)", reference.match_len_median, candidate.match_len_median),
    ):
        if _within(ref, cand, tolerance):
            result.matches.append(f"{name}: {ref} vs {cand}")
        else:
            result.differences.append(
                f"{name}: expected about {ref}, got {cand} "
                f"(outside +/-{tolerance:.0%}, which suggests a different game rather "
                "than sampling noise)"
            )
    return result


def _within(reference: float, candidate: float, tolerance: float) -> bool:
    if reference == 0:
        return candidate == 0
    return abs(candidate - reference) / abs(reference) <= tolerance


def render(reference: Fingerprint, candidate: Fingerprint, comparison: Comparison) -> str:
    """Both fingerprints side by side, so a person reads the comparison."""
    lines = [
        f"{'':32}{reference.game:>26}{candidate.game:>26}",
        "-" * 84,
    ]
    rows = [
        ("action kinds", lambda f: ", ".join(f.action_kinds) or "-"),
        ("component count", lambda f: str(f.component_count)),
        ("actions/turn median", lambda f: f"{f.actions_per_turn_median:g}"),
        ("actions/turn max", lambda f: str(f.actions_per_turn_max)),
        ("match length min/med/max",
         lambda f: f"{f.match_len_min}/{f.match_len_median:g}/{f.match_len_max}"),
        ("terminal rate", lambda f: f"{f.terminal_rate:.0%}"),
        ("winners", lambda f: str(f.winner_balance)),
    ]
    for label, get in rows:
        lines.append(f"{label:32}{get(reference):>26}{get(candidate):>26}")

    lines.append("")
    lines.append("VERDICT: " + ("consistent" if comparison.ok else "DIFFERENT GAME"))
    for difference in comparison.differences:
        lines.append(f"  ! {difference}")
    return "\n".join(lines)


def state_key_report(module: ModuleType, seed: int = 0) -> str:
    """The generated module's own state vocabulary, for the human approval step.

    Not a check - the schema is the compiler's to choose. It is printed because the
    golden cases have to be authored against whatever it picked, and a reviewer needs
    to see it.
    """
    state = module.initial_state(seed)
    if not isinstance(state, dict):
        return f"state is {type(state).__name__}, not a dict"
    lines = [f"{module.NAME} state keys (seed {seed}):"]
    for key, value in sorted(state.items()):
        preview = repr(value)
        lines.append(f"  {key:20} {type(value).__name__:10} {preview[:70]}")
    actions = module.legal_actions(state)
    lines.append(f"  -> {len(actions)} legal actions, {state.get(TO_PLAY)} to play")
    if actions:
        example = {k: v for k, v in actions[0].items() if k != LABEL}
        lines.append(f"  -> example action: {example}")
    return "\n".join(lines)
