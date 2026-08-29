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

from automa import config
from automa.compiler.generate import compile_game
from automa.compiler.ingest import IngestError
from automa.trajectory import TrajectoryWriter

RULE = "-" * 74

VIEW_NOTE = """\
## On hidden information

`describe_state(state, viewer)` renders the state for one viewer and must reveal nothing \
that viewer may not see. Viewers are `public`, `automa` and `human`. Automa's own hand is \
deliberately open - the person at the table has to play Automa's side physically - but \
the human's hand is theirs alone, and anything face-down is nobody's. Never include the \
seed or any value derived from it: the deal comes from the seed, so printing it hands \
over the opponent's cards.\
"""


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
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--matches", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--out", type=Path, default=None, help="staging path")
    parser.add_argument("--install", type=Path, default=None, help="where to install on approval")
    parser.add_argument("--yes", action="store_true", help="skip the approval prompt")
    args = parser.parse_args(argv)

    staging = args.out or Path("build") / f"{args.game}_compiled.py"
    install_to = args.install or Path("src/automa/games") / f"{args.game}_compiled.py"

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

    with TrajectoryWriter(f"compile-{args.game}", config=config.describe()) as traj:
        try:
            result = compile_game(
                args.game,
                material=args.material,
                extra_material=extra,
                view_note=VIEW_NOTE,
                out_path=staging,
                trajectory=traj,
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

    print(f"\n{RULE}\nSTATE SCHEMA THE COMPILER CHOSE\n{RULE}")
    print(result.report.get("state_keys", "(none reported)"))

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

    install_to.parent.mkdir(parents=True, exist_ok=True)
    install_to.write_text(staging.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"installed: {install_to}")
    print(f"\nNext:\n  .venv\\Scripts\\python -m automa.scripts.validate_games {install_to.stem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
