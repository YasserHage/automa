"""The Compiler: rule material in, a validated game module out.

This is the phase the whole product claim rests on. Everywhere else the project uses a
model to *choose* among options the core enumerated; here a model writes code that the
core will then trust every turn. `plan.md` puts it that way deliberately - error is
concentrated in compilation precisely because compilation is auditable, and every gate
below exists to audit it.

Every gate is **game-agnostic**. Nothing in the loop compares the result to a reference
implementation, because in the product there is none - the user submits rule material for
a game nobody has written, which is the whole point. A compiler that could only validate
games someone had already implemented would validate nothing that matters. The Phase 4
fingerprint comparison against the hand-written DNUP is a measuring instrument for that
one phase, applied after compilation, and it is never a gate.

The loop is generate -> validate -> repair. What makes it work is that the validation is
specific: the Phase 1 self-play harness tags every violation with the seed that produced
it, so a repair message says "seed 37, ply 12: card conservation - 25 cards accounted
for, expected 26" rather than "self-play failed". A model repairs from the first and
flounders on the second.

Every attempt - prompt, returned source, each validation failure, the repair message -
lands in the trajectory. That record *is* the Compiler's agent-trajectory deliverable,
and it is the reason the loop is written here rather than hidden inside `call_llm`'s
retry: these retries are worth reading.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automa.compiler import ingest
from automa.compiler.sandbox import DEFAULT_TIMEOUT_S, scan_source, validate_in_subprocess
from automa.contracts import CompilerOutput
from automa.core import game as game_contract
from automa.llm import call_llm
from automa.llm.call import LLMCallError
from automa.trajectory import TrajectoryWriter

SYSTEM = """\
You are a compiler. You are given the rule material for a physical board or card game \
and you produce a single self-contained Python module that implements it exactly.

The module is not a description of the game - it is the referee. A deterministic engine \
will import it and call it every turn to enumerate what is legal, and a language model \
will only ever choose among the options your code produces. If your module allows a move \
the rules forbid, nothing downstream can catch it. Precision matters far more than \
elegance or brevity.

Implement the rules that are actually in the material. Where the material is silent or \
ambiguous, choose the reading that makes the game playable, and say so in `assumptions` \
rather than inventing a rule and presenting it as fact. Where you genuinely cannot \
resolve something, put it in `open_questions` - a human reviews both before your module \
is ever used.\
"""

REQUIREMENTS = """\
## Hard requirements

1. A single self-contained module. Permitted imports are only: random, itertools, \
dataclasses, typing, collections, math, json, copy, enum, functools, operator, string, \
and `automa.core.game`. No file, network, process or import machinery - no `os`, `sys`, \
`open`, `exec`, `eval`, `__import__`.
2. State and every action must be plain JSON-round-trippable dicts: dict, list, str, \
int, float, bool, None. No tuples, no sets, no dataclass instances *inside the state* - \
they do not survive the round trip and the harness checks.
3. `apply` MUST NOT mutate the state it is given. Build and return a new one. The \
harness deep-compares the input after every call.
4. Every state carries `to_play` (a member of PLAYERS), `turn` (player turns taken, \
non-decreasing) and `ply` (strictly increasing on every apply).
5. Every action carries a `label` key: a short human-readable sentence. A person reads \
these at the table.
6. `legal_actions` must never return an empty list unless the state is terminal.
7. `winner` returns None only for a draw, and raises ValueError if the state is not \
terminal.
8. Implement `check_invariants`: return a list of strings describing anything \
structurally wrong with a state - components that have gone missing, negative \
quantities, contradictions. This is how the harness catches conservation bugs, so make \
it thorough. Return an empty list when the state is sound.
9. Any shuffling happens once, inside `initial_state`, seeded from its `seed` argument. \
`apply` must be a pure function of (state, action) with no randomness.
10. Keep the components (the deck, the pieces) in a module-level data table, separate \
from the logic that uses them, so the catalogue can later be extended without touching \
the rules.

Return the module as `module_source`, plus a plain-language summary of the rules you \
implemented, the assumptions you had to make, and anything you could not resolve.\
"""


@dataclass
class Attempt:
    """One pass through generate -> validate."""

    number: int
    stage: str
    problems: list[str] = field(default_factory=list)
    source: str = ""
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.stage == "passed"


@dataclass
class CompileResult:
    game: str
    ok: bool
    attempts: list[Attempt] = field(default_factory=list)
    output: CompilerOutput | None = None
    path: Path | None = None
    report: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        head = "compiled" if self.ok else "FAILED"
        return f"{head} after {len(self.attempts)} attempt(s)"


def contract_text() -> str:
    """The interface, taken from the source of truth rather than restated.

    Reading it out of `core/game.py` means the contract shown to the compiler cannot
    drift from the contract `conforms()` enforces - and a compiler generating against a
    stale description would fail for a reason that has nothing to do with the rules.
    """
    protocol = inspect.getsource(game_contract.GameModule)
    return (
        "## The interface your module must implement\n\n"
        "```python\n"
        f"{protocol}\n"
        "```\n\n"
        "Implement these as plain module-level functions (not a class), plus two "
        "module-level constants:\n\n"
        "```python\n"
        'NAME = "<game name>"\n'
        f"PLAYERS = {game_contract.DEFAULT_PLAYERS!r}\n"
        "```\n\n"
        "You may import the shared key names from `automa.core.game`:\n"
        f"`TO_PLAY = {game_contract.TO_PLAY!r}`, `TURN = {game_contract.TURN!r}`, "
        f"`PLY = {game_contract.PLY!r}`, `LABEL = {game_contract.LABEL!r}`, "
        "and the `Viewer` enum "
        f"({', '.join(v.value for v in game_contract.Viewer)})."
    )


def build_prompt(
    game: str,
    *,
    pages: list[ingest.PageImage],
    extra_material: str,
    view_note: str,
) -> list[dict[str, Any]]:
    """The compile request: instructions, the contract, then the rule material itself."""
    text = "\n\n".join(
        [
            f"Compile the game '{game}' into a Python module.",
            contract_text(),
            REQUIREMENTS,
            view_note,
            extra_material,
            "The rulebook pages follow as images. Read them carefully - they are the "
            "authority on the rules.",
        ]
    )
    return [{"role": "user", "content": ingest.as_message_parts(pages, text)}]


def compile_game(
    game: str,
    *,
    material: Path,
    extra_material: str = "",
    view_note: str = "",
    out_path: Path,
    trajectory: TrajectoryWriter,
    max_attempts: int = 4,
    matches: int = 200,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    call_attempts: int = 5,
) -> CompileResult:
    """Generate, validate and repair until the module passes every gate or attempts run out."""
    pages = ingest.pdf_page_images(material)
    trajectory.emit(
        "core_event",
        event="rule_material_ingested",
        game=game,
        source=str(material),
        pages=[p.describe() for p in pages],
        extra_material_chars=len(extra_material),
    )

    messages = build_prompt(game, pages=pages, extra_material=extra_material, view_note=view_note)
    result = CompileResult(game=game, ok=False)

    output: CompilerOutput | None = None

    for number in range(1, max_attempts + 1):
        try:
            call = call_llm(
                agent="compiler",
                system=SYSTEM,
                messages=messages,
                output_format=CompilerOutput,
                max_tokens=32768,
                # Generous: a compile is one long call against a heavily congested
                # model, and losing a whole generation to a transient 503 wastes far
                # more than the retries cost.
                max_attempts=call_attempts,
                trajectory=trajectory,
                tags={"game": game, "attempt": number},
            )
        except LLMCallError as exc:
            # The provider gave up, not the model. Record it as an attempt so the run
            # is honest about what happened, and try again rather than losing the
            # whole compile - the same failure that killed a DNUP match in Phase 2.
            attempt = Attempt(number=number, stage="unreachable", problems=[str(exc)])
            _record(trajectory, attempt)
            result.attempts.append(attempt)
            continue

        output = call.parsed
        source = _strip_fences(output.module_source)

        attempt = Attempt(number=number, stage="scan", source=source,
                          summary=output.plain_language_summary)

        # Gate 1: read it before running it.
        scan = scan_source(source, filename=str(out_path))
        if not scan.ok:
            attempt.problems = scan.problems
            _record(trajectory, attempt)
            result.attempts.append(attempt)
            messages = _repair(messages, output, attempt)
            continue

        # Gates 2-4: everything that executes, executes in a child process.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(source, encoding="utf-8")
        verdict = validate_in_subprocess(out_path, matches=matches, timeout_s=timeout_s)
        attempt.stage = verdict.get("stage", "unknown")
        attempt.problems = list(verdict.get("problems") or [])
        _record(trajectory, attempt, verdict=verdict)
        result.attempts.append(attempt)

        if verdict.get("ok"):
            result.ok = True
            result.output = output
            result.path = out_path
            result.report = verdict
            return result

        messages = _repair(messages, output, attempt)

    # Out of attempts: leave the last source on disk for a human to look at.
    result.output = output
    result.path = out_path if out_path.exists() else None
    return result


def _strip_fences(source: str) -> str:
    """Models fence code by habit. That is formatting, not a compilation error."""
    text = source.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if "\n" in body:
        first, rest = body.split("\n", 1)
        if first.strip().lower() in {"python", "py", ""}:
            body = rest
    return body.rsplit("```", 1)[0].strip() if "```" in body else body.strip()


def _record(trajectory: TrajectoryWriter, attempt: Attempt, verdict: dict | None = None) -> None:
    trajectory.emit(
        "core_event",
        event="compile_attempt",
        attempt=attempt.number,
        stage=attempt.stage,
        ok=attempt.ok,
        problems=attempt.problems,
        source_chars=len(attempt.source),
        outcomes=(verdict or {}).get("outcomes"),
        fingerprint=(verdict or {}).get("fingerprint"),
    )


def _repair(
    messages: list[dict[str, Any]], output: CompilerOutput, attempt: Attempt
) -> list[dict[str, Any]]:
    """Feed the failure back with enough detail to act on.

    The whole value of this loop is here. Each problem names the gate, and where the
    self-play harness produced it, the seed and ply that reproduce it - so the next
    attempt is a correction rather than another roll of the dice.
    """
    listed = "\n".join(f"  - {p}" for p in attempt.problems[:12])
    more = (
        f"\n  ...and {len(attempt.problems) - 12} more of the same kind."
        if len(attempt.problems) > 12
        else ""
    )
    guidance = {
        "scan": "Remove the disallowed construct. A game module needs only state manipulation.",
        "import": "The module does not import. Fix the error and keep it self-contained.",
        "conforms": "The interface is not satisfied. Check names, arity and the constants.",
        "selfplay": (
            "Random self-play found real defects. Each line gives the seed and ply that "
            "reproduce it. Reason about what rule produced that state rather than "
            "special-casing the symptom."
        ),
        "timeout": "The module did not terminate. Make sure every match reaches an end.",
        "crash": "Validation crashed. Look at the error and make the module robust.",
    }.get(attempt.stage, "Fix the problems listed.")

    return [
        *messages,
        {"role": "assistant", "content": output.module_source[:4000]},
        {
            "role": "user",
            "content": (
                f"That module failed at the '{attempt.stage}' gate:\n{listed}{more}\n\n"
                f"{guidance}\n\n"
                "Return the complete corrected module again - the whole file, not a diff "
                "or a fragment."
            ),
        },
    ]
