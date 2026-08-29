"""Summarise match trajectories into comparable metrics.

    python -m automa.scripts.match_stats trajectories/*.jsonl --json out.json

Reads finished matches out of the JSONL logs and reports, per match: outcome, model,
tokens, cost, and latency. `plan.md` names cost per turn and latency per turn as
secondary metrics for Phase 3, so this exists to produce them from evidence rather
than from anybody's notes.

**Costs are recomputed, not read back.** Each trajectory records raw `input_tokens`
and `output_tokens` per call alongside the cost that was calculated at the time. If
the price table was wrong when a match ran - as it was while Automa was assumed to be
on a free tier - the tokens still allow the true figure to be recovered afterwards.
The report shows both, so a corrected number is never mistaken for the original.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from automa.llm.pricing import cost_usd, pricing_note


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarise(path: Path) -> dict[str, Any] | None:
    """One match, or ``None`` if the log holds no finished match."""
    events = read(path)
    end = next((e for e in events if e.get("event") == "match_end"), None)
    start = next((e for e in events if e.get("event") == "match_start"), None)
    if end is None or start is None:
        return None

    calls = [e for e in events if e["type"] == "llm_call"]
    ok = [e for e in calls if e["outcome"] == "ok"]
    model = Counter(e["model"] for e in ok).most_common(1)[0][0] if ok else None

    moves = [e for e in events if e.get("event") == "action_applied"]
    automa_moves = [e for e in moves if e.get("player") == "automa"]
    tin = sum(e.get("input_tokens") or 0 for e in ok)
    tout = sum(e.get("output_tokens") or 0 for e in ok)
    lat = sorted((e.get("latency_ms") or 0) / 1000 for e in ok)

    recomputed = cost_usd(model, input_tokens=tin, output_tokens=tout) if model else None
    logged = sum(e.get("cost_usd") or 0 for e in ok)

    return {
        "file": path.name,
        "game": start.get("game"),
        "seed": start.get("seed"),
        "model": model,
        "outcome": end.get("outcome"),
        "winner": end.get("winner"),
        "turns": end.get("turns"),
        "plies": end.get("plies"),
        "automa_moves": len(automa_moves),
        "justified_moves": sum(1 for e in automa_moves if e.get("justification")),
        "fallbacks": end.get("fallbacks"),
        "human_typed_moves": sum(1 for e in events if e["type"] == "human_checkpoint"),
        "llm_calls_ok": len(ok),
        "llm_attempts_failed": len(calls) - len(ok),
        "input_tokens": tin,
        "output_tokens": tout,
        "output_tokens_median": statistics.median(
            [e["output_tokens"] for e in ok]
        ) if ok else None,
        "cost_usd_as_logged": round(logged, 6),
        "cost_usd_recomputed": round(recomputed, 6) if recomputed is not None else None,
        "cost_usd_per_turn": round(recomputed / len(ok), 6) if recomputed and ok else None,
        "pricing_note": pricing_note(model) if model else None,
        "latency_s_p50": round(statistics.median(lat), 1) if lat else None,
        "latency_s_mean": round(statistics.mean(lat), 1) if lat else None,
        "latency_s_max": round(lat[-1], 1) if lat else None,
    }


def table(rows: list[dict[str, Any]]) -> str:
    cols = [
        ("game", "game"), ("model", "model"), ("winner", "winner"), ("turns", "turns"),
        ("llm_calls_ok", "calls"), ("cost_usd_recomputed", "cost $"),
        ("cost_usd_per_turn", "$/turn"), ("latency_s_p50", "p50 s"),
        ("latency_s_mean", "mean s"), ("latency_s_max", "max s"),
    ]
    out = [" | ".join(h for _, h in cols), "-|-".join("-" * len(h) for _, h in cols)]
    for r in rows:
        out.append(" | ".join(str(r.get(k, "")) for k, _ in cols))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = [s for p in args.paths if (s := summarise(p)) is not None]
    if not rows:
        print("No finished matches found in those trajectories.", file=sys.stderr)
        return 1

    print(table(rows))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"matches": rows}, indent=2), encoding="utf-8")
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
