"""Compile rule material into a game module, with a human gate before it is installed.

    python -m automa.scripts.compile_game --game dnup \\
        --material files/dnup_rules.pdf \\
        --cards src/automa/compiler/material/dnup_cards.md \\
        --compare-with dnup                              (or: automa-compile)

The compiler writes to a staging path and validates it there. Only after a person reads
the plain-language summary, the assumptions and the open questions does the module get
installed into `automa/games/` where the loader will find it.

That gate is not ceremony. `plan.md` concentrates error in compilation *because*
compilation is auditable, and a compiler whose output is installed without anyone reading
it has thrown away the property the whole architecture is built on. `--yes` skips the
prompt for scripted runs, and says so in the output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from typing import Any

from automa import config
from automa.compiler.generate import VIEW_NOTE, catalog_requirements, compile_game
from automa.compiler.ingest import IngestError
from automa.compiler.install import InstallRefused, install_compiled_game, plan_install
from automa.trajectory import TrajectoryWriter

RULE = "-" * 74


class PrintingTrajectory(TrajectoryWriter):
    """Prints a live checkpoint for every retry, error and gate result.

    `compile_game` can run for minutes and exposes no callback of its own; the CLI
    used to print nothing until it returned, so a stuck call and one about to
    succeed looked identical. `web/services/compiling.py` solves the same problem
    for its SSE stream by subclassing `TrajectoryWriter` and reacting to `emit()`
    rather than tailing the JSONL - this does the same for a terminal. `emit()`
    still writes the trajectory file exactly as the base class would (invariant 3);
    printing happens after, purely as a side effect.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._seen_attempts: set[int] = set()

    def emit(self, event_type: str, **fields: Any) -> int:
        seq = super().emit(event_type, **fields)
        self._checkpoint(event_type, fields)
        return seq

    def _checkpoint(self, event_type: str, fields: dict[str, Any]) -> None:
        if event_type == "llm_call":
            number = (fields.get("tags") or {}).get("attempt")
            if number is not None and number not in self._seen_attempts:
                self._seen_attempts.add(number)
                print(f"\nattempt {number}: waiting for the model...", flush=True)
            outcome = fields.get("outcome")
            call_try = fields.get("attempt")
            if outcome == "api_error":
                print(
                    f"    ! call failed (try {call_try}): {fields.get('error')} - retrying",
                    flush=True,
                )
            elif outcome == "validation_failed":
                print(
                    f"    ! response rejected (try {call_try}): "
                    f"{fields.get('rejection')} - retrying",
                    flush=True,
                )
            elif outcome == "ok":
                latency = fields.get("latency_ms") or 0
                print(
                    f"  attempt {number}: model responded ({latency:.0f}ms) - validating...",
                    flush=True,
                )
        elif event_type == "core_event" and fields.get("event") == "compile_attempt":
            number = fields.get("attempt")
            mark = "PASS" if fields.get("ok") else f"FAILED at {fields.get('stage')}"
            print(f"  attempt {number}: {mark}", flush=True)
            for problem in (fields.get("problems") or [])[:3]:
                print(f"      - {str(problem)[:150]}", flush=True)

# `VIEW_NOTE` moved to `compiler/generate.py`, beside the other prompt constants, once
# the web UI became a second thing that compiles. Re-exported here so anything that
# imported it from this script still works, and so the two callers cannot drift.
__all__ = ["VIEW_NOTE", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", required=True, help="name for the compiled module")
    parser.add_argument("--material", type=Path, required=True, help="rulebook PDF")
    parser.add_argument("--cards", type=Path, default=None, help="extra material (text)")
    parser.add_argument(
        "--compare-with",
        default=None,
        help=(
            "OPTIONAL, and never a gate: fingerprint the result against an installed "
            "game and print the comparison for review. Only useful where a reference "
            "implementation happens to exist, which in the product it does not."
        ),
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help=(
            "the game learns card effects at runtime: instruct the compiler to read "
            "them from a catalog and to raise `unknown` for cards nobody has described"
        ),
    )
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--matches", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--out", type=Path, default=None, help="staging path")
    parser.add_argument("--install", type=Path, default=None, help="where to install on approval")
    parser.add_argument(
        "--draft-catalog",
        action="store_true",
        help=(
            "after installing, read the material again to draft what each card does. "
            "Only useful where the material states card effects; whatever it does not "
            "state is asked at the table instead"
        ),
    )
    parser.add_argument("--yes", action="store_true", help="skip the approval prompt")
    args = parser.parse_args(argv)

    staging = args.out or Path("build") / f"{args.game}_compiled.py"

    extra = ""
    if args.cards:
        if not args.cards.exists():
            print(f"no such file: {args.cards}", file=sys.stderr)
            return 1
        extra = (
            "## Additional submitted material\n\n"
            "The rulebook does not list the cards; the user supplied them separately.\n\n"
            + args.cards.read_text(encoding="utf-8")
        )

    print(f"{RULE}\ncompiling {args.game}")
    print(f"model: {config.model_for('compiler')}   attempts: {args.attempts}")
    print(RULE)

    with PrintingTrajectory(f"compile-{args.game}", config=config.describe()) as traj:
        try:
            result = compile_game(
                args.game,
                material=args.material,
                extra_material=extra,
                view_note=VIEW_NOTE,
                out_path=staging,
                trajectory=traj,
                catalog_note=catalog_requirements() if args.catalog else "",
                max_attempts=args.attempts,
                matches=args.matches,
                timeout_s=args.timeout,
            )
        except IngestError as exc:
            print(exc, file=sys.stderr)
            return 1
        path, cost, calls = traj.path, traj.total_cost_usd, traj.llm_calls

    for attempt in result.attempts:
        mark = "PASS" if attempt.ok else f"failed at {attempt.stage}"
        print(f"\nattempt {attempt.number}: {mark}  ({len(attempt.source):,} chars)")
        for problem in attempt.problems[:6]:
            print(f"    - {str(problem)[:150]}")
        if len(attempt.problems) > 6:
            print(f"    ... and {len(attempt.problems) - 6} more")

    print(f"\n{RULE}\n{result.summary()}")
    print(f"calls: {calls}   cost: ${cost:.4f}   trajectory: {path}")

    if not result.ok:
        print(f"\nLast attempt left at {staging} for inspection.", file=sys.stderr)
        return 1

    # Evidence for a reviewer, never a gate. Compilation has already succeeded or failed
    # on grounds that need no reference implementation - which is the only way a compiler
    # for arbitrary submitted games can work. This block exists because Phase 4 happens
    # to have a hand-written DNUP to measure against; no game a user submits will.
    if args.compare_with:
        from automa.compiler import fingerprint as fp
        from automa.core.loader import load_game

        candidate = fp.Fingerprint(**result.report["fingerprint"])
        # The reference is trusted code, so it is fingerprinted here. The candidate's
        # numbers came back from the sandbox, because generated code never runs in this
        # process.
        reference = fp.collect(load_game(args.compare_with), matches=40)
        comparison = fp.compare(reference, candidate)
        print(f"\n{RULE}\nFINGERPRINT vs {args.compare_with}   (evidence, not a gate)\n{RULE}")
        print(fp.render(reference, candidate, comparison))
        if comparison.differences:
            print("\nIt passed every gate but behaves differently from the reference.")
            print("That is a judgement call for you, not a failure.")

    asked = result.report.get("unknowns")
    if asked is not None:
        note = "" if asked else "   (nothing was ever described - see the summary)"
        print(f"\nquestions asked during validation: {asked}{note}")

    print(f"\n{RULE}\nSTATE SCHEMA THE COMPILER CHOSE\n{RULE}")
    print(result.report.get("state_keys", "(none reported)"))

    installed_name = result.report.get("name") or args.game
    plan = plan_install(staging, installed_name=installed_name, install_to=args.install)
    install_to = plan.target

    output = result.output
    print(f"\n{RULE}\nWHAT IT SAYS IT IMPLEMENTED\n{RULE}")
    print(output.plain_language_summary)
    if output.assumptions:
        print("\nAssumptions it had to make:")
        for item in output.assumptions:
            print(f"  - {item}")
    if output.open_questions:
        print("\nOpen questions it could not resolve:")
        for item in output.open_questions:
            print(f"  - {item}")

    print(f"\n{RULE}")
    if args.yes:
        print("--yes given: installing without review. The summary above was NOT read by a human.")
    else:
        print("Read the summary, the assumptions and the open questions above.")
        print("Every gate below this point is automated; this one is not.")
        try:
            answer = input(f"\nInstall to {install_to}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in {"y", "yes"}:
            print(f"Not installed. The module stays at {staging}.")
            return 0

    overwrite = False
    if plan.differs:
        print(f"\n{install_to} already exists and differs from this build.")
        try:
            overwrite = input("Overwrite it? [y/N] ").strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            overwrite = False
        if not overwrite:
            print(f"Not installed. The module stays at {staging}.")
            return 0

    try:
        install_compiled_game(plan, overwrite=overwrite)
    except InstallRefused as exc:
        print(exc, file=sys.stderr)
        print(f"Not installed. The module stays at {staging}.", file=sys.stderr)
        return 1
    print(f"installed: {install_to}   (playable as --game {installed_name})")

    wants_catalog = args.draft_catalog and args.cards
    if wants_catalog:
        print(f"\n{RULE}\nDRAFTING THE CATALOG\n{RULE}")
        from automa.scripts import build_catalog

        code = build_catalog.main(
            [
                "--game", installed_name,
                "--material", str(args.material),
                "--cards", str(args.cards),
            ]
        )
        if code:
            print(
                "\nThe catalog was not drafted. That is not fatal: every card will be "
                "asked about at the table instead.",
                file=sys.stderr,
            )
        return 0

    print("\nNext:")
    print(f"  .venv\\Scripts\\python -m automa.scripts.validate_games {installed_name}")
    if args.cards:
        print(
            f"  .venv\\Scripts\\python -m automa.scripts.build_catalog --game "
            f"{installed_name} \\\n      --material {args.material} --cards {args.cards}"
        )
    print(f"  .venv\\Scripts\\python -m automa.scripts.play --game {installed_name} --catalog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
