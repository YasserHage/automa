"""Run the evaluation: the same questions, to the baseline and to the agent.

    python -m automa.eval.run --target baseline
    python -m automa.eval.run --target agent
                                                        (or: automa-eval)

Both targets see identical cases and identical match seeds, so the two result files
are directly comparable. `automa.eval.report` renders the comparison.

Two things are measured, because one is not enough:

* **The case battery** asks a single question from a fixed position with known ground
  truth. This is where rule understanding shows up cleanly, isolated from luck.
* **Full matches** are the only place `plan.md`'s primary metric - turns before a
  human would have to intervene - can exist at all.

The agent scoring zero illegal actions is the architectural claim, not a discovery:
the core enumerates and the Tactician returns an index, so an illegal action cannot be
expressed. The report says so rather than presenting it as a result.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from automa import config
from automa.agents.tactician import tactician_decider
from automa.core.game import AUTOMA
from automa.core.loader import load_game
from automa.core.match import Decision, TurnContext, play_match, random_decider, viewer_for
from automa.eval import cases as case_lib
from automa.eval.baseline import baseline_decider, match_action
from automa.trajectory import TrajectoryWriter

TARGETS = ("baseline", "agent")


def _decider(target: str, seed: int):
    return baseline_decider(seed=seed) if target == "baseline" else tactician_decider()


# -- the case battery ------------------------------------------------------


def run_cases(target: str, game: str | None, trajectory: TrajectoryWriter) -> list[dict[str, Any]]:
    """Ask each fixed position once, and score the answer against ground truth."""
    results = []
    for resolved in case_lib.resolved_battery(game):
        c = resolved.case
        module = load_game(c.game)
        ctx = TurnContext(
            game=module,
            state=resolved.state,
            player=resolved.to_play,
            viewer=viewer_for(resolved.to_play),
            actions=resolved.legal,
            turn=resolved.state["turn"],
            ply=c.ply,
            trajectory=trajectory,
        )
        trajectory.emit("core_event", event="case_start", case=c.case_id, category=c.category)

        started = time.monotonic()
        decision = _decider(target, seed=c.seed)(ctx)
        elapsed = round(time.monotonic() - started, 2)
        if isinstance(decision, int):
            decision = Decision(index=decision)

        chosen = resolved.legal[decision.index]
        # The agent cannot be illegal by construction; the baseline can, and an
        # intervention is exactly how that surfaces.
        legal = not decision.intervention
        optimal = (
            match_action(chosen, resolved.optimal) is not None
            if resolved.has_optimal and legal
            else None
        )
        results.append(
            {
                "case_id": c.case_id,
                "game": c.game,
                "category": c.category,
                "legal_action_count": len(resolved.legal),
                "optimal_action_count": len(resolved.optimal),
                "legal": legal,
                "optimal": optimal,
                "fallback": decision.fallback,
                "chosen": chosen.get("label"),
                "justification": decision.justification,
                "seconds": elapsed,
            }
        )
    return results


# -- full matches ----------------------------------------------------------


def run_match(target: str, game_name: str, seed: int, directory: Path | None) -> dict[str, Any]:
    """One match, the target playing Automa's side against a seeded random opponent."""
    game = load_game(game_name)
    opponent = next(p for p in game.PLAYERS if p != AUTOMA)
    with TrajectoryWriter(
        f"eval-{target}-{game_name}",
        directory=directory,
        config=config.describe(),
        seeds={"match": seed, "target": target},
    ) as traj:
        result = play_match(
            game,
            {AUTOMA: _decider(target, seed=seed), opponent: random_decider(seed)},
            seed=seed,
            trajectory=traj,
        )
        path = str(traj.path)
        calls, cost = traj.llm_calls, traj.total_cost_usd

    interventions = result.interventions.get(AUTOMA, 0)
    return {
        "game": game_name,
        "seed": seed,
        "winner": result.winner,
        "won": result.winner == AUTOMA,
        "turns": result.turns,
        "outcome": result.outcome,
        "interventions": interventions,
        "fallbacks": result.fallbacks.get(AUTOMA, 0),
        # plan.md's primary metric. With no intervention the whole match was clean, so
        # the honest reading is "every turn it played".
        "turns_without_intervention": (
            result.first_intervention_turn if interventions else result.turns
        ),
        "clean_match": interventions == 0,
        "llm_calls": calls,
        "cost_usd": round(cost, 6),
        "trajectory": path,
    }


def run_matches(
    target: str, plan: list[tuple[str, int]], workers: int, directory: Path | None
) -> list[dict[str, Any]]:
    """Matches are independent and seeded, so they parallelise without coordination."""
    if workers <= 1:
        return [run_match(target, g, s, directory) for g, s in plan]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_match, target, g, s, directory) for g, s in plan]
        return [f.result() for f in futures]


# -- aggregation -----------------------------------------------------------


def summarise(cases: list[dict], matches: list[dict]) -> dict[str, Any]:
    scored = [c for c in cases if not c["fallback"]]
    quality = [c for c in scored if c["optimal"] is not None]
    per_game: dict[str, Any] = {}
    for game in sorted({m["game"] for m in matches}):
        rows = [m for m in matches if m["game"] == game]
        per_game[game] = {
            "matches": len(rows),
            "wins": sum(m["won"] for m in rows),
            "clean_matches": sum(m["clean_match"] for m in rows),
            "interventions_per_match": round(
                statistics.mean([m["interventions"] for m in rows]), 2
            ),
            "turns_without_intervention_mean": round(
                statistics.mean([m["turns_without_intervention"] for m in rows]), 1
            ),
            "cost_per_turn": round(
                sum(m["cost_usd"] for m in rows) / max(sum(m["llm_calls"] for m in rows), 1), 6
            ),
        }
    return {
        "cases": {
            "n": len(cases),
            "legal": sum(c["legal"] for c in scored),
            "legal_rate": round(sum(c["legal"] for c in scored) / max(len(scored), 1), 3),
            "quality_n": len(quality),
            "optimal": sum(bool(c["optimal"]) for c in quality),
            "optimal_rate": round(
                sum(bool(c["optimal"]) for c in quality) / max(len(quality), 1), 3
            ),
            "fallbacks": sum(c["fallback"] for c in cases),
        },
        "matches": per_game,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--game", default=None, help="restrict to one game")
    parser.add_argument("--nim-matches", type=int, default=10)
    parser.add_argument("--dnup-matches", type=int, default=3)
    parser.add_argument("--cases-only", action="store_true")
    parser.add_argument("--matches-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--trajectory-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    started = datetime.now(UTC)
    cases: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    if not args.matches_only:
        with TrajectoryWriter(
            f"eval-{args.target}-cases",
            directory=args.trajectory_dir,
            config=config.describe(),
        ) as traj:
            cases = run_cases(args.target, args.game, traj)
        done = sum(c["legal"] for c in cases)
        print(f"cases: {done}/{len(cases)} legal")

    if not args.cases_only:
        plan: list[tuple[str, int]] = []
        for game, count in (("nim", args.nim_matches), ("dnup", args.dnup_matches)):
            if args.game in (None, game):
                plan += [(game, seed) for seed in range(count)]
        matches = run_matches(args.target, plan, args.workers, args.trajectory_dir)
        print(f"matches: {len(matches)} played")

    report = {
        "target": args.target,
        "model": config.model_for(args.target if args.target == "baseline" else "tactician"),
        "started": started.isoformat(timespec="seconds"),
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        "summary": summarise(cases, matches),
        "case_results": cases,
        "match_results": matches,
    }
    print(json.dumps(report["summary"], indent=2))

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
