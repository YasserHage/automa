"""Render two evaluation runs side by side.

    python -m automa.eval.report docs/validation/phase3_baseline.json \
                                 docs/validation/phase3_agent.json

The numbers that carry information are the case battery and, on Nim, decision
quality - both arms can genuinely differ there. Illegal actions are reported too, but
the agent scoring zero is the architectural claim rather than a finding: the core
enumerates and the Tactician answers with an index, so an illegal action has no way to
be expressed. This renderer labels that row as structural so nobody reads it as a
surprise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

STRUCTURAL = "structural, not measured - the agent cannot express an illegal action"


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def render(runs: dict[str, dict[str, Any]]) -> str:
    names = list(runs)
    w = 26
    out: list[str] = []

    def row(label: str, values: list[str], note: str = "") -> None:
        line = f"{label:<34}" + "".join(f"{v:>{w}}" for v in values)
        out.append(line + (f"   # {note}" if note else ""))

    out.append("=" * (34 + w * len(names)))
    row("", [n for n in names])
    out.append("-" * (34 + w * len(names)))
    row("model", [runs[n].get("model", "?").split(":")[-1] for n in names])

    out.append("")
    out.append("CASE BATTERY (fixed positions, ground truth from the game module)")
    cases = {n: runs[n]["summary"]["cases"] for n in names}
    row("legal moves proposed", [_pct(cases[n]["legal"], cases[n]["n"] - cases[n]["fallbacks"])
                                 for n in names])
    row("optimal moves (Nim only)", [_pct(cases[n]["optimal"], cases[n]["quality_n"])
                                     for n in names])
    row("unreachable-model fallbacks", [str(cases[n]["fallbacks"]) for n in names])

    # Per-category legality, which is where a specific rule turns out to be the one
    # being missed rather than "the rules" in general.
    categories = sorted({c["category"] for n in names for c in runs[n]["case_results"]})
    if categories:
        out.append("")
        out.append("  by category (legal moves proposed)")
        for cat in categories:
            vals = []
            for n in names:
                rows = [c for c in runs[n]["case_results"]
                        if c["category"] == cat and not c["fallback"]]
                vals.append(_pct(sum(r["legal"] for r in rows), len(rows)))
            row(f"    {cat}", vals)

    games = sorted({g for n in names for g in runs[n]["summary"]["matches"]})
    for game in games:
        out.append("")
        out.append(f"MATCHES - {game}")
        got = {n: runs[n]["summary"]["matches"].get(game) for n in names}
        if not all(got.values()):
            out.append("  (not run for every target)")
            continue
        row("matches played", [str(got[n]["matches"]) for n in names])
        row("turns w/o intervention (mean)",
            [f"{got[n]['turns_without_intervention_mean']}" for n in names],
            "primary metric")
        row("interventions per match",
            [f"{got[n]['interventions_per_match']}" for n in names],
            STRUCTURAL)
        row("clean matches", [_pct(got[n]["clean_matches"], got[n]["matches"]) for n in names])
        row("wins vs random", [_pct(got[n]["wins"], got[n]["matches"]) for n in names],
            "not headline: a win can follow a losing move")
        row("cost per turn", [f"${got[n]['cost_per_turn']:.6f}" for n in names])

    out.append("")
    out.append("=" * (34 + w * len(names)))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--markdown", type=Path, default=None)
    args = parser.parse_args(argv)

    runs: dict[str, dict[str, Any]] = {}
    for path in args.paths:
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 1
        data = json.loads(path.read_text(encoding="utf-8"))
        runs[data["target"]] = data

    text = render(runs)
    print(text)
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(f"```\n{text}\n```\n", encoding="utf-8")
        print(f"\nwritten: {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
