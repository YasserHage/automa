"""Play a match against Automa in the terminal.

    python -m automa.scripts.play --game dnup            (or: automa-play)
    python -m automa.scripts.play --game dnup --simulate  deal from the internal deck
    python -m automa.scripts.play --game dnup --auto     unattended, random opponent

**The cards are the ones on the table.** By default the game asks what was actually
dealt and plays with that; ``--simulate`` (and ``--auto``, which has nobody to ask) falls
back to the game dealing itself a hand from ``--seed``, which is a testing path only.

This is the Phase 2 exit criterion: a complete match, on either game, with every
tactician decision in the trajectory - the prompt it saw, the actions it was offered
and the index it returned.

``--auto`` replaces the person at the keyboard with a seeded random opponent. That is
not only a convenience for demonstrating a long DNUP match without typing seventy
turns - it is the same wiring Phase 3 needs to run a battery non-interactively, which
is why the match loop takes a decider per player rather than assuming a human.

Each hand is shown only to its owner, Automa's included. That reverses an earlier locked
decision to keep Automa's hand open: the physical argument for it was real - somebody has
to play Automa's side at the table - but on screen it hands the opponent perfect
information, and the trajectory already makes every move auditable after the fact.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time

from automa import config
from automa.agents.tactician import tactician_decider
from automa.catalog import store
from automa.catalog.informant import catalog_informant
from automa.core.game import AUTOMA, LABEL
from automa.core.loader import GameLoadError, available_games, load_game
from automa.core.match import (
    Decision,
    MatchAbandoned,
    TurnContext,
    compose,
    play_match,
    random_decider,
    uninformed,
)
from automa.table import Question, table_informant
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


def thinking(decider):
    """Say that Automa is working before it starts, not only once it has finished.

    Automa moves first in some games, so the first thing after the banner can be a model
    call taking anywhere from three seconds to a minute - and on a congested free tier,
    three silent retries on top of that. With nothing on screen, a working match is
    indistinguishable from a crashed one. It was, the first time this was run.
    """

    def _decide(ctx: TurnContext):
        print("  Automa is thinking...", end="", flush=True)
        try:
            return decider(ctx)
        finally:
            print("\r" + " " * 30 + "\r", end="", flush=True)

    return _decide


def announce(ctx: TurnContext, decision: Decision, action: dict) -> None:
    """Narrate Automa's move. Keeps terminal I/O out of the deterministic core."""
    if ctx.player != AUTOMA:
        return
    print(f"\n  Automa (turn {ctx.turn}) plays: {action[LABEL]}")
    if decision.fallback:
        # A legal move, but nobody chose it. Saying so is the point of counting
        # fallbacks separately: a silent substitution reads as a bad decision by the
        # tactician rather than as the model having been unreachable.
        print("  (the model could not be reached - this move was picked at random)")


#: Card types this match had to put to a person. The count is the Phase 5 metric:
#: a second match of the same game should need fewer.
_LEARNED: list[str] = []


def ask_at_the_table(prompt: str) -> str | None:
    """Put the module's question to the person, in their own words.

    Deliberately free text. The person describes the card as they would to another
    player; turning that into operations is the cataloguer agent's job, and asking
    somebody to type a vocabulary operation would defeat the point of having one.

    An empty answer is a real answer - "I cannot say" - and parks the card rather than
    asking again every turn.
    """
    print(f"\n{RULE}")
    print("Automa needs to know something only you can see.")
    print("This is information, not a move - it is not counted against you.")
    print(f"\n  {prompt}")
    print("\nDescribe it in your own words, or press Enter if you cannot say.")
    try:
        return input("> ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None


def never_asks(_prompt: str) -> None:
    """Unattended: nothing is described, and the match must finish anyway."""
    return None


def ask_components_at_the_table(question: Question) -> str | None:
    """Ask the person to read a group of components off the table.

    The default way a match starts: the cards are the ones physically dealt, not a
    shuffle the module invented. Only the count is checked, and it is checked here
    rather than in the module, so a miscount is corrected in the one place where it can
    be - in front of the person who can look again.
    """
    print(f"\n{RULE}")
    print("Automa needs to know what is on the table.")
    print("This is information, not a move - it is not counted against you.")
    if question.note:
        print(f"\n  {question.note}.")
    print()
    print(textwrap.fill(question.prompt, width=76, initial_indent="  ", subsequent_indent="  "))
    if question.options:
        shown = ", ".join(question.options[:20])
        rest = "" if len(question.options) <= 20 else f", ... ({len(question.options)} in all)"
        print(f"\n  names this game knows: {shown}{rest}")
    print("\nSeparate them with spaces or commas, or press Enter if you cannot say.")
    try:
        return input("> ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None


#: Question kinds nobody could answer this match. Printed at the end, because a game
#: played with un-modelled cards should say so on the screen and not only in the log.
_UNANSWERED: list[str] = []


def reporting(informant):
    """Wrap an informant so a question that went unanswered is visible afterwards."""

    def _ask(ctx, unknown):
        answer = informant(ctx, unknown)
        if answer.get("unavailable"):
            _UNANSWERED.append(str(unknown.get("kind")))
        return answer

    return _ask


def report_learned(card_type: str, stored: bool) -> None:
    _LEARNED.append(card_type)
    if stored:
        print(f"  recorded: {card_type}")
    else:
        print(f"  parked:   {card_type} (kept as a known gap, never asked again)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play a match against Automa.")
    parser.add_argument("--game", required=True, help=f"one of: {', '.join(available_games())}")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "seed the fallback deal, for a repeatable match. Only used where the table "
            "supplies nothing - see --simulate"
        ),
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help=(
            "do not ask what was dealt; let the game deal its own cards from --seed. "
            "For testing a game without the physical deck in front of you"
        ),
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
    parser.add_argument(
        "--catalog",
        action="store_true",
        help=(
            "the game learns card effects at the table: answer the questions it asks "
            "and they are remembered, so later matches ask fewer"
        ),
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
        automa_player: thinking(paced(tactician_decider(), args.delay)),
        human_player: opponent,
    }

    catalog = store.load(game.NAME) if args.catalog else None

    print(f"{RULE}\n{game.NAME} - you are {human_player}, Automa is {automa_player}")
    print(f"model: {config.model_for('tactician')}"
          + ("   [--auto: random opponent]" if args.auto else ""))
    print(
        "cards: dealt by the game itself (--simulate)"
        if args.auto or args.simulate
        else "cards: read off the table - you will be asked what was dealt"
    )
    if catalog is not None:
        known, parked = len(catalog.known()), len(catalog.parked())
        where = catalog.path or store.CATALOG_DIR / f"{game.NAME}.json"
        print(
            f"catalog: {known} card(s) known, {parked} parked  ({where})"
            if len(catalog)
            else f"catalog: empty - every card will be asked about once  ({where})"
        )
    print(RULE)

    with TrajectoryWriter(game.NAME, config=config.describe(), seeds={"match": args.seed}) as traj:
        # Built here, inside the writer, because the Cataloguer reaches a model and
        # invariant 3 says every prompt and response is logged. Constructing the
        # informant earlier left it holding no trajectory, so a card described at the
        # table was structured by a model call that went unrecorded - the one kind of
        # call the record most needs, since it is where a person's words enter the game.
        #
        # An informant is always supplied. A game that asks what was dealt cannot start
        # without one, and the default answer to "what is on the table" is the table.
        channels = []
        if not (args.auto or args.simulate):
            channels.append(table_informant(ask_components_at_the_table))
        if catalog is not None:
            channels.append(
                catalog_informant(
                    catalog,
                    ask=never_asks if args.auto else ask_at_the_table,
                    trajectory=traj,
                    on_learn=report_learned,
                )
            )
        informant = reporting(compose(*channels) if channels else uninformed)
        result = play_match(
            game,
            deciders,
            seed=args.seed,
            trajectory=traj,
            observer=announce,
            informant=informant,
            max_plies=args.max_plies,
        )
        path = traj.path
        calls, cost = traj.llm_calls, traj.total_cost_usd

    print(f"\n{RULE}")
    print(result.summary())
    if result.invariant_violations:
        print(f"WARNING: {len(result.invariant_violations)} invariant violation(s) during play")
    if catalog is not None:
        print(
            f"catalog: {len(catalog.known())} known, {len(catalog.parked())} parked   "
            f"asked about {len(_LEARNED)} new card(s) this match"
        )
    if _UNANSWERED:
        counted = {kind: _UNANSWERED.count(kind) for kind in sorted(set(_UNANSWERED))}
        detail = ", ".join(f"{kind} x{n}" for kind, n in counted.items())
        print(f"unanswered: {detail} - the game played on without those facts")
    print(f"tactician calls: {calls}   cost: ${cost:.6f}")
    print(f"trajectory: {path}")
    print(f'\nRead it with:\n  python -m automa.trajectory.show "{path}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
