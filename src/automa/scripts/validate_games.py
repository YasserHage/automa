"""Phase 1 exit criterion: random self-play across every game the loader can find.

    python -m automa.scripts.validate_games --matches 500     (or: automa-validate)

Prints a per-game summary and exits non-zero if any game fails, so the criterion is
something CI could enforce rather than something a person eyeballs. ``--json`` writes
the full report, including every violation and the seed that produced it - the
hackathon requires result claims to be tied to submitted evidence, and a number in a
terminal that scrolled away is not evidence.

No model is involved anywhere in this path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from automa.core.loader import available_games, load_game
from automa.core.selfplay import DEFAULT_MAX_PLIES, validate_module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "games",
        nargs="*",
        help="games to validate (default: every game the loader finds)",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=500,
        help="random matches per game (default: 500, the plan.md exit criterion)",
    )
    parser.add_argument("--seed0", type=int, default=0, help="first seed (default: 0)")
    parser.add_argument(
        "--max-plies",
        type=int,
        default=DEFAULT_MAX_PLIES,
        help=f"abort a match after this many actions (default: {DEFAULT_MAX_PLIES})",
    )
    parser.add_argument("--json", type=Path, default=None, help="write the full report here")
    args = parser.parse_args(argv)

    names = args.games or available_games()
    if not names:
        print("No games found under automa.games.", file=sys.stderr)
        return 1

    reports = []
    for name in names:
        module = load_game(name)
        report = validate_module(
            module,
            matches=args.matches,
            seed0=args.seed0,
            max_plies=args.max_plies,
        )
        reports.append(report)
        print(report.summary())

    failed = [r.game for r in reports if not r.ok]

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "matches_per_game": args.matches,
                    "seed0": args.seed0,
                    "max_plies": args.max_plies,
                    "ok": not failed,
                    "games": [r.to_dict() for r in reports],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nreport written to {args.json}")

    if failed:
        print(f"\nFAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nAll {len(reports)} game(s) passed {args.matches} random matches each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
