"""Play a match against Automa in the terminal.

    python -m automa.scripts.play --game dnup            (or: automa-play)
    python -m automa.scripts.play --game nim --seed 1
    python -m automa.scripts.play --game dnup --auto     unattended, random opponent

This is the Phase 2 exit criterion: a complete match, on either game, with every
tactician decision and its justification in the trajectory.

``--auto`` replaces the person at the keyboard with a seeded random opponent. That is
not only a convenience for demonstrating a long DNUP match without typing seventy
turns - it is the same wiring Phase 3 needs to run a battery non-interactively, which
is why the match loop takes a decider per player rather than assuming a human.

The human's own hand is shown only to the human. Automa's hand is shown to everyone,
which is a locked decision: the person at the table has to be able to play Automa's
side physically, and it makes every move auditable.
"""

from __future__ import annotations

import argparse
import sys
import time

from automa import config
from automa.agents.tactician import tactician_decider
from automa.core.game import AUTOMA, LABEL
from automa.core.loader import GameLoadError, available_games, load_game
from automa.core.match import (
    Decision,
    MatchAbandoned,
    TurnContext,
    play_match,
    random_decider,
)
from automa.trajectory import TrajectoryWriter

RULE = "-" * 70


def human_decider():
    """Read a choice from the terminal, re-prompting until it is a legal index."""

    def decide(ctx: TurnContext) -> Decision:
        print(f"\n{RULE}\nYour turn - {ctx.game.NAME}, turn {ctx.turn}\n{RULE}")
        print(ctx.view())
        print("\nYour legal actions:")
        print(ctx.numbered())

        while True:
            try:
                raw = input(f"\nChoose 0-{len(ctx.actions) - 1} (or q to quit): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raise MatchAbandoned from None

            if raw.lower() in {"q", "quit", "exit"}:
                raise MatchAbandoned
            if raw.isdigit() and 0 <= int(raw) < len(ctx.actions):
                index = int(raw)
                if ctx.trajectory is not None:
                    ctx.trajectory.emit(
                        "human_checkpoint",
                        event="human_turn",
                        ply=ctx.ply,
                        turn=ctx.turn,
                        shown=ctx.view(),
                        options=[a[LABEL] for a in ctx.actions],
                        chosen_index=index,
                        chosen=ctx.actions[index][LABEL],
                    )
                return Decision(index=index)
            print(f"  '{raw}' is not one of 0-{len(ctx.actions) - 1}. Try again.")

    return decide


def paced(decider, seconds: float):
    """Wrap a decider so it waits before deciding - pacing for a rate-limited tier."""
    if seconds <= 0:
        return decider

    def _decide(ctx: TurnContext):
        time.sleep(seconds)
        return decider(ctx)

    return _decide


def announce(ctx: TurnContext, decision: Decision, action: dict) -> None:
    """Narrate Automa's move. Keeps terminal I/O out of the deterministic core."""
    if ctx.player != AUTOMA:
        return
    print(f"\n  Automa (turn {ctx.turn}) plays: {action[LABEL]}")
    if decision.justification:
        print(f"  because: {decision.justification}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play a match against Automa.")
    parser.add_argument("--game", required=True, help=f"one of: {', '.join(available_games())}")
    parser.add_argument(
        "--seed", type=int, default=None, help="seed the deal, for a repeatable match"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="play the human side with a seeded random opponent, unattended",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="seconds to wait before each Automa turn, to pace a rate-limited tier",
    )
    parser.add_argument("--max-plies", type=int, default=1000)
    args = parser.parse_args(argv)

    try:
        game = load_game(args.game)
    except GameLoadError as exc:
        print(exc, file=sys.stderr)
        return 1

    automa_player = AUTOMA if AUTOMA in game.PLAYERS else game.PLAYERS[0]
    human_player = next(p for p in game.PLAYERS if p != automa_player)

    opponent = random_decider(args.seed) if args.auto else human_decider()
    deciders = {
        automa_player: paced(tactician_decider(), args.delay),
        human_player: opponent,
    }

    print(f"{RULE}\n{game.NAME} - you are {human_player}, Automa is {automa_player}")
    print(f"model: {config.model_for('tactician')}"
          + ("   [--auto: random opponent]" if args.auto else ""))
    print(RULE)

    with TrajectoryWriter(game.NAME, config=config.describe(), seeds={"match": args.seed}) as traj:
        result = play_match(
            game,
            deciders,
            seed=args.seed,
            trajectory=traj,
            observer=announce,
            max_plies=args.max_plies,
        )
        path = traj.path
        calls, cost = traj.llm_calls, traj.total_cost_usd

    print(f"\n{RULE}")
    print(result.summary())
    if result.invariant_violations:
        print(f"WARNING: {len(result.invariant_violations)} invariant violation(s) during play")
    print(f"tactician calls: {calls}   cost: ${cost:.6f}")
    print(f"trajectory: {path}")
    print(f'\nRead it with:\n  python -m automa.trajectory.show "{path}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
