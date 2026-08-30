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
6. `legal_actions` must never return an empty list unless the state is terminal. \
If you find yourself wanting to return a single do-nothing action so that the list is \
non-empty, the state machine is wrong: either the state is terminal, or it is waiting on \
something that is not a player's decision and belongs in `unknown` or `pending`.
7. `winner` returns None only for a draw, and raises ValueError if the state is not \
terminal.
8. Implement `check_invariants`: return a list of strings describing anything \
structurally wrong with a state - components that have gone missing, negative \
quantities, contradictions. This is how the harness catches conservation bugs, so make \
it thorough. Return an empty list when the state is sound.
9. The components are the physical ones on the table - see below. Where you must deal \
them yourself, that happens once, inside `initial_state`, seeded from its `seed` \
argument. `apply` must be a pure function of (state, action) with no randomness.
10. Keep the components (the deck, the pieces) in a module-level data table, separate \
from the logic that uses them, so the catalogue can later be extended without touching \
the rules.
11. The interface is strictly sequential: one `to_play`, one action at a time. A game \
in which players act **simultaneously** is modelled as hidden sequential choice - the \
first player's action is recorded in the state, `to_play` passes to the second, and the \
first choice must not be visible in the second player's view until both have chosen. \
Each is a real `apply` and increments `ply`; the shared consequence resolves on the \
second. `turn` counts rounds of play, so it advances once for the pair, not twice.

Return the module as `module_source`, plus a plain-language summary of the rules you \
implemented, the assumptions you had to make, and anything you could not resolve.\
"""


COMPONENTS = """\
## The components come from the physical table

This game is played with real cards in front of a real person, and your module is the
opponent's referee. The cards that get dealt are the ones on that table. If you shuffle
a deck of your own and deal from it, the opponent plays a hand nobody is holding and
every move it makes is about a different game - so **do not invent the deal.** Ask.

1. Whenever your module needs to know which components are somewhere - the hand just
   dealt, the card just drawn, the tile just turned over, the face a die showed - set:

   `state["unknown"] = {{"kind": "components", "slot": <name>, "count": <how many>,
   "prompt": <what to read out>}}`

   and return the state as it stands, with that group still empty. `slot` names the
   group and is echoed back to you in the answer, so several questions stay apart:
   `"hands.automa"`, `"draw"`, `"die"`. `count` is how many things you expect, and it is
   the only thing anybody checks - a person who types the wrong number is asked again
   before the answer ever reaches you. `prompt` is read aloud at the table, so say what
   to read out and in what form, with an example.

   You may add `"options": [<names>]` when your game has a fixed vocabulary of component
   names; it is shown to the person as a hint. It is never a filter - a name you do not
   recognise still reaches you.

2. **Ask for a group, never for one component at a time.** A hand of thirteen cards is
   one question with `count: 13`, not thirteen questions. Only eight questions may be
   asked before the engine calls it a loop.

3. **Never ask twice for the same group.** Track what has already been supplied in the
   state - an empty hand is not proof that a hand has not been dealt, since emptying a
   hand is how many games are won.

4. Implement `resolve_unknown(state, answer) -> State`, clearing the `unknown` and
   advancing neither `turn` nor `ply`. Two shapes arrive and both must work:

   - `{{"slot": <name>, "components": ["1/5", "2/9", ...]}}` - what the table holds, as
     plain strings, exactly as the person typed them (lower-cased, spaces turned into
     underscores). Give them meaning yourself: match them onto your component table.
     A string that matches nothing is not an error to raise - keep it, play on, and let
     `check_invariants` report it.
   - `{{"slot": <name>, "unavailable": True}}` - nobody could say. **Now** you deal that
     group yourself, from the seeded shuffle, and carry on.

   The `unavailable` branch is not an error path and not an afterthought: random
   self-play validates your module by answering every question that way, so it is the
   path that has to work perfectly or your module never reaches a table at all.

5. A state waiting on its components is **sound, not broken**. `check_invariants` is
   called on it before anything is answered, so do not report the components you have
   not been given yet as missing.

6. `initial_state` therefore usually returns a state that is already asking a question,
   with the hands empty. That is correct and expected. The engine resolves every
   question before it asks anyone to move, so `legal_actions` never sees one.\
"""


VIEW_NOTE = """\
## On hidden information

`describe_state(state, viewer)` renders the state for one viewer and must reveal nothing \
that viewer may not see. Viewers are `public`, `automa` and `human`.

**Each player's hidden information is theirs alone.** A hand, a face-down card, a secret \
choice already made: show it in that player's own view and in no other. In the `public` \
view show only what is on the table, and a count rather than the contents of anything \
concealed. This applies to Automa exactly as it applies to the person - Automa's hand \
belongs in the `automa` view and nowhere else, or the human plays with perfect \
information and the match is spoiled.

Never include the seed or any value derived from it: the deal comes from the seed, so \
printing it hands over both players' cards.\
"""
"""What every compile is told about hidden information.

Lives here, beside the other prompt constants, rather than in the CLI that used to own
it. Two callers now compile - the script and the web UI - and if they each held their
own copy of this text, a web-compiled module could end up on a different side of the
hand-privacy decision from a CLI-compiled one, silently reversing a locked decision.
"""


#: Given to the compiler only for games whose card effects are learned rather than
#: known. Generated from the vocabulary itself, so what the module is told to implement
#: cannot drift from what the catalog will actually store.
def catalog_requirements(supported: set[str] | None = None) -> str:
    """Instructions for a module whose card effects come from a catalog.

    The point of separating the two: a module knows the *rules* - turn order, legality,
    the scoring procedure - and the catalog knows what individual *cards* do. They are
    learned at different times from different places, the rules once from a rulebook and
    the cards one at a time from the person at the table. A module that hardcoded
    "tempura scores 5 for every 2" would have to be recompiled to learn a new card.
    """
    from automa.catalog.vocabulary import describe_vocabulary

    return f"""\
## Card effects come from a catalog, not from your code

Do NOT hardcode what any individual card does. The rules of the game are yours to
implement; what each card is worth is looked up at runtime from a catalog the player
fills in as cards appear.

1. `initial_state` accepts the catalog through the state it builds. The state carries:
   `state["catalog"] = {{"effects": {{<card type>: [<operations>]}}, "described": [<card types>]}}`
   `effects` is what you execute. `described` is every card type someone has already
   been asked about - including ones that could not be expressed - and you must never
   ask about those again.

2. When play reveals a card type that is in neither, set the state key `unknown`:
   `state["unknown"] = {{"kind": "card_effect", "card_type": <type>, "prompt": <question>}}`
   and return the state as it stands. Leave `unknown` absent or None the rest of the time.

   **The engine answers it before anyone is asked to move.** After `initial_state` and
   after every `apply`, the engine looks for `unknown` and calls `resolve_unknown` until
   it is gone - so `legal_actions` is NEVER called on a state carrying an `unknown`.
   Do not invent a "wait" or "resolve" action for a player to choose. Answering a
   question is not a move: there is nothing to choose between, and such an action cannot
   advance `ply` without lying about what happened. Setting `unknown` is how you stop.

3. Answer it in the same `resolve_unknown(state, answer) -> State` that takes your
   `components` answers - there is one such function, and it **dispatches on the kind of
   question it asked**. It returns a state with the `unknown` **cleared**, and it
   advances neither `turn` nor `ply` - being told something is not a turn. Two more
   shapes of answer arrive here, and both must work:

   - `{{"card_type": <type>, "effects": [<operations>]}}` - record them in the catalog.
   - `{{"card_type": <type>, "unavailable": True}}` - nobody could describe the card, or
     the description did not fit the vocabulary. Record it in `described` so it is never
     asked about again, treat it as having no effect, and say in `describe_state` that it
     is un-modelled. Play continues without it.

   The `unavailable` case is not an error path. A person can always fail to answer, and
   **random self-play validates your module by answering every question that way** - it
   has no person to ask. A module that cannot finish a match when nothing is ever
   described will not pass validation.

4. Declare `SUPPORTED_OPS`: the set of operation names your scoring code actually
   interprets. A card needing an operation you do not implement will be parked rather
   than silently ignored.

   Declare `TERMS` alongside it: every *name* your code looks for inside an operation,
   grouped by the field it appears in. For example:

   `TERMS = {{"flag": {{"chopsticks"}}, "count_field": {{"icons"}}, "category": {{"nigiri"}}}}`

   These are the values your code compares against as strings - a marker your rules
   test, a quantity you total instead of counting card copies, a group of cards you
   recognise, a resource you track. They are the only places where the catalog and your
   module can disagree while both stay internally valid, and the disagreement never
   raises: you look for a name nobody wrote, and your rule silently never fires. Whatever
   names you choose are the names a person will be offered, so declare them and then
   accept exactly those. Omit a field you never use, and prefer naming a card's marker
   after the card type itself.

5. Interpret operations as *data*. Read the fields off the dict; never evaluate a
   string as code and never branch on a card name where you could branch on an
   operation.

{describe_vocabulary(supported)}
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
        f"`PLY = {game_contract.PLY!r}`, `UNKNOWN = {game_contract.UNKNOWN!r}`, "
        f"`LABEL = {game_contract.LABEL!r}`, `SLOT = {game_contract.SLOT!r}`, "
        f"`COUNT = {game_contract.COUNT!r}`, `OPTIONS = {game_contract.OPTIONS!r}`, "
        f"`COMPONENTS = {game_contract.COMPONENTS!r}`, "
        f"`UNAVAILABLE = {game_contract.UNAVAILABLE!r}`.\n\n"
        # Two compiles in a row wrote `Viewer.public` and each lost a whole repair round
        # to it. They were shown the enum's *values* and left to infer the member names,
        # so lowercase was a fair inference from what they were given. Spell the members
        # out: guessing them is not the compiler's job.
        "The `Viewer` enum has these members. The names are UPPERCASE; the lowercase\n"
        "spellings are the values, not attributes:\n\n"
        "```python\n"
        + "\n".join(
            f"Viewer.{v.name}   # value {v.value!r}" for v in game_contract.Viewer
        )
        + "\n```"
    )


def build_prompt(
    game: str,
    *,
    material: ingest.Material,
    extra_material: str,
    view_note: str,
    catalog_note: str = "",
) -> list[dict[str, Any]]:
    """The compile request: instructions, the contract, then the rule material itself.

    Rule material arrives as text or as page images depending on the PDF, and the
    difference is invisible from here - :func:`ingest.read_material` already decided.
    Sending a scanned rulebook as text would send nothing at all; sending a text
    rulebook as images would send several hundred card thumbnails.
    """
    parts = [
        f"Compile the game '{game}' into a Python module.",
        contract_text(),
        REQUIREMENTS,
        # Not conditional on anything: every game is played with physical components,
        # whether or not its cards carry effects that have to be learned.
        COMPONENTS,
        catalog_note,
        view_note,
        extra_material,
    ]

    if material.text:
        parts.append(
            "The rulebook text follows. It is the authority on the rules.\n\n"
            "--- RULEBOOK ---\n" + material.text
        )
        return [{"role": "user", "content": "\n\n".join(p for p in parts if p)}]

    parts.append(
        "The rulebook pages follow as images. Read them carefully - they are the "
        "authority on the rules."
    )
    text = "\n\n".join(p for p in parts if p)
    return [{"role": "user", "content": ingest.as_message_parts(material.pages, text)}]


def compile_game(
    game: str,
    *,
    material: Path,
    extra_material: str = "",
    view_note: str = "",
    out_path: Path,
    trajectory: TrajectoryWriter,
    catalog_note: str = "",
    max_attempts: int = 4,
    matches: int = 200,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    call_attempts: int = 5,
) -> CompileResult:
    """Generate, validate and repair until the module passes every gate or attempts run out."""
    read = ingest.read_material(material)
    trajectory.emit(
        "core_event",
        event="rule_material_ingested",
        game=game,
        source=str(material),
        kind=read.kind,
        detail=read.describe(),
        extra_material_chars=len(extra_material),
    )

    messages = build_prompt(
        game,
        material=read,
        extra_material=extra_material,
        view_note=view_note,
        catalog_note=catalog_note,
    )
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

        if call.parsed is None:
            # A completed call that parsed to nothing. The usual cause is the answer
            # running into `max_tokens` mid-JSON: the provider returns text, the schema
            # never validates, and `parsed` is None. Treated as a failed attempt with a
            # message the model can act on, rather than an AttributeError three lines
            # later - which is exactly how this surfaced the first time.
            attempt = Attempt(
                number=number,
                stage="truncated",
                problems=[
                    f"the response did not parse into the required format "
                    f"(stop_reason={call.stop_reason!r}, {call.output_tokens} output "
                    "tokens). It was most likely cut off before the JSON closed."
                ],
            )
            _record(trajectory, attempt)
            result.attempts.append(attempt)
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "That response was cut off before it formed a complete answer. "
                        "Return the whole module again, and keep it compact - drop "
                        "comments and docstrings rather than leaving the code "
                        "unfinished."
                    ),
                },
            ]
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
        out_path.write_text(GENERATED_HEADER + source, encoding="utf-8")
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


#: Prepended to every generated module. The lint-suppression line is the load-bearing
#: one: compiler output is held to the *contract* - the interface, self-play soundness,
#: the sandbox - and not to this project's house style, because a model's import ordering
#: says nothing about whether the module plays the game correctly. It travels with the
#: file rather than living in a filename pattern in `pyproject.toml`, which is what broke
#: the moment generated modules started being installed under their own NAME.
GENERATED_HEADER = "\n".join(
    [
        "# Generated by the Automa compiler from submitted rule material.",
        "# Do not edit by hand: recompiling overwrites this file.",
        "# Reviewed and installed by a person before first use.",
        # Assembled so this line is not read as a directive for *this* file.
        "# ruff" + ": noqa",
        "",
    ]
)


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
