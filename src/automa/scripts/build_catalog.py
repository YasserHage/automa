"""Draft a game's card catalog from its rule material.

    python -m automa.scripts.build_catalog --game sushigo \\
        --material files/SushiGo_rules.pdf \\
        --cards src/automa/compiler/material/sushigo_cards.md

Run after the game is compiled and installed. Deliberately a separate command from
`compile_game`: compiling produces a module and drafting produces data, they read the
same material for different things, and either can be useful without the other. A game
whose card values are printed on the cards rather than in the rulebook compiles fine and
drafts nothing, and that is a normal outcome rather than a failure.

Whatever it cannot find is left for the table. The first time such a card is played the
match pauses, asks, and records the answer - so drafting moves questions out of a match
rather than removing the mechanism that handles them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from automa import config
from automa.catalog import store
from automa.catalog.build import CatalogBuildError, draft_catalog
from automa.compiler.ingest import IngestError
from automa.core.loader import GameLoadError, load_game
from automa.trajectory import TrajectoryWriter

RULE = "-" * 74


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", required=True, help="an installed game")
    parser.add_argument("--material", type=Path, required=True, help="rulebook PDF")
    parser.add_argument("--cards", type=Path, default=None, help="the card list, as submitted")
    parser.add_argument(
        "--out", type=Path, default=None, help="catalog path (default: catalog/<game>.json)"
    )
    parser.add_argument(
        "--forget",
        action="append",
        default=[],
        metavar="CARD",
        help=(
            "drop what is recorded for a card so it is asked about again. Use when an "
            "answer turned out to be wrong - a recorded answer is otherwise permanent, "
            "which is what stops the same question being asked every match"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what was read without writing the catalog",
    )
    args = parser.parse_args(argv)

    try:
        game = load_game(args.game)
    except GameLoadError as exc:
        print(exc, file=sys.stderr)
        print("Compile and install it first.", file=sys.stderr)
        return 1

    supported = getattr(game, "SUPPORTED_OPS", None)
    terms = store.terms_for(game)
    if supported is None:
        print(
            f"{game.NAME} does not declare SUPPORTED_OPS, so it does not read a "
            "catalog. Nothing to draft.",
            file=sys.stderr,
        )
        return 1

    # The module's own deck table is the ground truth for card-type names - a catalog
    # keyed to prose the model invented from the rulebook (e.g. "Maki roll") will never
    # match the string the module actually looks up ("1_maki_roll"). A submitted card
    # list, when there is one, is additional prose about those same cards, not a second
    # source of names to reconcile against.
    parts: list[str] = []
    card_types = store.card_types_for(game)
    if card_types:
        parts.append(
            "Use these names exactly - they are the game module's own card-type "
            "strings:\n" + "\n".join(f"- {name}" for name in sorted(card_types))
        )
    if args.cards:
        if not args.cards.exists():
            print(f"no such file: {args.cards}", file=sys.stderr)
            return 1
        parts.append(args.cards.read_text(encoding="utf-8"))
    card_material = "\n\n".join(parts)

    path = args.out or store.CATALOG_DIR / f"{game.NAME}.json"
    catalog = store.load(game.NAME, path)
    before = len(catalog)

    forgotten = [c for c in args.forget if catalog.forget(c)]
    missing = [c for c in args.forget if c not in forgotten]
    if forgotten:
        print(f"forgetting: {', '.join(sorted(forgotten))}")
        if not args.dry_run:
            catalog.save(path)
    if missing:
        print(f"not recorded, nothing to forget: {', '.join(sorted(missing))}", file=sys.stderr)

    print(f"{RULE}\ndrafting a catalog for {game.NAME}")
    print(f"model: {config.model_for('catalog_builder')}")
    if before:
        print(f"{before} card(s) already recorded; those are left alone")
    print(RULE)

    with TrajectoryWriter(f"catalog-{game.NAME}", config=config.describe()) as traj:
        try:
            catalog, draft = draft_catalog(
                game.NAME,
                material=args.material,
                card_material=card_material,
                catalog=catalog,
                supported=set(supported),
                terms=terms,
                trajectory=traj,
                save=not args.dry_run,
            )
        except (CatalogBuildError, IngestError) as exc:
            print(exc, file=sys.stderr)
            return 1
        traj_path, cost = traj.path, traj.total_cost_usd

    print(f"\n{RULE}\nWHAT IT READ\n{RULE}")
    for card in sorted(draft.cards, key=lambda c: c.card_type):
        entry = catalog.entries.get(card.card_type)
        if entry is None:
            continue
        if entry.playable:
            ops = ", ".join(op.get("op", "?") for op in entry.effects)
            print(f"  {card.card_type:<16} {ops}")
        else:
            print(f"  {card.card_type:<16} PARKED - {entry.unresolved[:60]}")
        if card.evidence:
            print(f"  {'':16} from: {card.evidence[:70]}")

    if draft.not_described:
        print("\nNot described in the material - a person will be asked at the table:")
        for name in sorted(draft.not_described):
            print(f"  {name}")
    if draft.notes:
        print(f"\nNotes: {draft.notes}")

    known, parked = len(catalog.known()), len(catalog.parked())
    print(f"\n{RULE}")
    print(f"{known} card(s) recorded, {parked} parked, {len(catalog) - before} new")
    print(f"cost: ${cost:.4f}   trajectory: {traj_path}")

    if args.dry_run:
        print("\n--dry-run: nothing was written.")
        return 0

    print(f"catalog: {path}")
    print(
        "\nRead the effects above against the rulebook before playing - this is the "
        "one step\nnobody checks for you. Then:\n"
        f"  .venv\\Scripts\\python -m automa.scripts.play --game {game.NAME} --catalog"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
