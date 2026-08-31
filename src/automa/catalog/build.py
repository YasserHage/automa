"""Draft a card catalog from the rule material, before anybody sits down to play.

Separate from the Compiler on purpose. The Compiler reads rule material and produces a
*module* - how a turn works, what is legal, how scoring is procedurally carried out. This
reads the same material and produces *data* - what each individual card is worth. They
are separate because they fail separately and are useful separately: a game whose rulebook
explains the turn order but prints the card values on the cards themselves compiles
perfectly and drafts nothing, and a game with no cards at all never runs this step. Fusing
them would make every game pay for both.

**A partial catalog is the expected outcome, not a failure.** Whatever the material states
is drafted; whatever it does not is left absent, and the first time such a card reaches the
table the informant asks a person. That is the same loop either way - drafting only moves
questions off the table and out of a match, it does not remove the mechanism.

The distinction this module exists to protect: **absent is not parked.**

- *Absent* - nobody has looked at this card yet. The table will ask about it.
- *Parked* - somebody looked and the closed vocabulary could not express it. Never asked
  about again, because asking a second time would waste a person's attention on a
  question already answered.

A card the rulebook simply never mentions must come out absent. Parking it would make it
permanently unaskable, which is the one outcome worse than not knowing: the system would
believe it had settled a question nobody ever answered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from automa.catalog.store import Catalog
from automa.catalog.vocabulary import (
    describe_terms,
    describe_vocabulary,
    unsupported,
    validate,
)
from automa.compiler import ingest
from automa.contracts import CatalogDraft
from automa.llm import call_llm
from automa.llm.call import LLMCallError
from automa.trajectory import TrajectoryWriter

SYSTEM = """\
You read the rule material for a card game and record what each card is worth, as data.

You are not interpreting the rules of play - another process has already done that. Your \
only job is the cards: for each one the material actually describes, express its effect \
as operations drawn from a closed vocabulary.

Not every card scores. A card that changes what its owner may *do* - take two cards \
instead of one, ignore a restriction, act out of order - is recorded with `set_flag`, \
which raises a named marker the compiled rules already test for. Recording such a card as \
inexpressible leaves the module's own rule for it switched off, so the mechanic silently \
never happens.

Watch for a family of card types that all feed one shared comparison - the material \
often describes them together in a single passage ("add up the X on every card of this \
kind; whoever has the most scores..."), and each variant may be worth a different amount \
even though the comparison is scored once. `score_majority` and `score_at_end` only total \
what `gain` operations add up: give **every** card in such a family its own `gain` \
alongside its `score_majority`/`score_at_end`, each with the amount that specific card is \
worth. Recording the comparison without a `gain` on each contributing card is a silent \
zero, not a smaller number - the total never moves no matter what is collected.

Record only what the material states. If it does not say what a card does, leave that \
card out; somebody will be asked about it at the table, which is a normal part of how \
this system works and is far better than a guess. Do not infer a card's value from its \
name, from another game you know, or from what would be balanced. A card recorded wrongly \
plays a silently different game; a card left out costs one question.\
"""


class CatalogBuildError(RuntimeError):
    """The material could not be read, or the model could not be reached."""


def build_prompt(
    *,
    material: ingest.Material,
    card_material: str,
    supported: set[str] | None,
    terms: dict[str, set[str]] | None,
    already_known: set[str],
) -> list[dict[str, Any]]:
    """The drafting request: the vocabulary, the deck, then the material itself."""
    parts = [
        describe_vocabulary(supported),
        describe_terms(terms),
    ]
    if card_material:
        parts.append(
            "## The cards in this game\n\n"
            "Use these names exactly - the module looks effects up by them.\n\n"
            + card_material
        )
    if already_known:
        parts.append(
            "## Already recorded\n\nThese are done; do not draft them again: "
            + ", ".join(sorted(already_known))
        )
    parts.append(
        "Record every card the material describes. Leave out any card it does not, and "
        "list those in `not_described`."
    )

    if material.text:
        parts.append("--- RULE MATERIAL ---\n" + material.text)
        return [{"role": "user", "content": "\n\n".join(p for p in parts if p)}]

    parts.append("The rule material follows as page images.")
    text = "\n\n".join(p for p in parts if p)
    return [{"role": "user", "content": ingest.as_message_parts(material.pages, text)}]


def malformed(
    draft: CatalogDraft,
    supported: set[str] | None,
    terms: dict[str, set[str]] | None,
) -> dict[str, list[str]]:
    """Cards whose operations will not execute, and why.

    Only structural problems count here. A card the model declined with
    ``cannot_express`` is a real answer, and an operation this game does not implement is
    a real verdict about the card - neither is worth re-asking.
    """
    out: dict[str, list[str]] = {}
    for card in draft.cards:
        if card.cannot_express:
            continue
        effects = [op.model_dump(exclude_none=True) for op in card.effects]
        if not effects:
            continue
        problems = validate(effects)
        if problems:
            out[card.card_type] = problems
    return out


def _repair_request(draft: CatalogDraft, problems: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Ask again for just the cards that came back unusable."""
    listed = "\n".join(
        f"  - {card}: {'; '.join(reasons)}" for card, reasons in sorted(problems.items())
    )
    return [
        {"role": "assistant", "content": draft.model_dump_json()[:4000]},
        {
            "role": "user",
            "content": (
                f"These cards came back with operations that will not execute:\n{listed}"
                "\n\nReturn a draft containing ONLY those cards, corrected. Fill in the "
                "missing fields from what the material says. If the material does not "
                "give a number, leave the card out entirely rather than guessing - "
                "somebody will be asked at the table, which is fine."
            ),
        },
    ]


def draft_catalog(
    game: str,
    *,
    material: Path,
    card_material: str = "",
    catalog: Catalog | None = None,
    supported: set[str] | None = None,
    terms: dict[str, set[str]] | None = None,
    trajectory: TrajectoryWriter | None = None,
    max_attempts: int = 3,
    save: bool = True,
) -> tuple[Catalog, CatalogDraft]:
    """Read ``material`` and record what it says about each card.

    Returns the catalog and the raw draft, so a caller can show a person what was read
    and from which phrase before any of it is used in a match.

    Never raises for a card it cannot express - that card is parked, and the rest are
    kept. The only failures are a material that cannot be read and a provider that
    cannot be reached.
    """
    catalog = catalog if catalog is not None else Catalog(game=game)
    read = ingest.read_material(material)

    if trajectory is not None:
        trajectory.emit(
            "core_event",
            event="catalog_material_ingested",
            game=game,
            source=str(material),
            kind=read.kind,
            detail=read.describe(),
        )

    messages = build_prompt(
        material=read,
        card_material=card_material,
        supported=supported,
        terms=terms,
        already_known=catalog.described(),
    )

    try:
        result = call_llm(
            agent="catalog_builder",
            system=SYSTEM,
            messages=messages,
            output_format=CatalogDraft,
            max_tokens=8192,
            max_attempts=max_attempts,
            trajectory=trajectory,
            tags={"game": game},
        )
    except LLMCallError as exc:
        raise CatalogBuildError(
            f"could not draft a catalog for {game}: {exc}. Nothing was written; every "
            "card will be asked about at the table, which is how a game with no "
            "usable material is played anyway."
        ) from exc

    draft: CatalogDraft | None = result.parsed
    if draft is None:
        raise CatalogBuildError(
            "the model's answer did not parse into a catalog draft (it was most likely "
            "cut off). Nothing was written."
        )

    # One repair round, for the cards that came back unusable. Bounded at one because
    # the second attempt is where a model either fills in what it missed or does not
    # have it; a third would mostly buy guesses. Everything drafted correctly the first
    # time is untouched, so a bad card costs one call rather than the whole draft.
    broken = malformed(draft, supported, terms)
    if broken:
        if trajectory is not None:
            trajectory.emit(
                "core_event",
                event="catalog_draft_repair",
                game=game,
                cards=sorted(broken),
                problems={k: v for k, v in sorted(broken.items())},
            )
        try:
            repair = call_llm(
                agent="catalog_builder",
                system=SYSTEM,
                messages=[*messages, *_repair_request(draft, broken)],
                output_format=CatalogDraft,
                max_tokens=4096,
                max_attempts=max_attempts,
                trajectory=trajectory,
                tags={"game": game, "repair": True},
            )
        except LLMCallError:
            repair = None  # keep what already works; the rest goes to the table

        if repair is not None and repair.parsed is not None:
            fixed = {c.card_type: c for c in repair.parsed.cards}
            draft = draft.model_copy(
                update={"cards": [fixed.get(c.card_type, c) for c in draft.cards]}
            )

    for card in draft.cards:
        if card.card_type in catalog.described():
            continue

        if card.cannot_express:
            # The material described it and the vocabulary could not hold it. That is a
            # real answer, so it is parked and never asked about again.
            catalog.park(card.card_type, card.evidence, card.cannot_express)
            continue

        effects = [op.model_dump(exclude_none=True) for op in card.effects]
        if not effects:
            # Claimed as described but carrying nothing. Left absent rather than parked:
            # the model told us nothing, so nobody has actually answered this question.
            continue

        # Only `unsupported` is a verdict about the card: this game does not implement
        # the operation its effect needs, and a person describing it at the table would
        # reach the same wall. That is worth parking.
        blocked = unsupported(effects, supported, terms)
        if blocked:
            catalog.park(card.card_type, card.evidence, "; ".join(blocked))
            continue

        # A malformed operation is a verdict about the *answer*, not the card. It has
        # already survived a retry to get here, so it is left absent and the table asks
        # - which is the whole point of there being a table. Parking it would mark a
        # question answered that nobody answered.
        if validate(effects):
            continue

        problems = catalog.learn(
            card.card_type,
            effects=effects,
            description=card.evidence,
            supported=supported,
            terms=terms,
        )
        if problems:  # refused by the store: leave absent rather than assume
            continue

    if save and len(catalog):
        catalog.save()

    if trajectory is not None:
        trajectory.emit(
            "core_event",
            event="catalog_drafted",
            game=game,
            known=sorted(catalog.known()),
            parked=sorted(catalog.parked()),
            not_described=sorted(draft.not_described),
            note=(
                "Cards left absent are not gaps in the catalog - they are the questions "
                "the table will answer."
            ),
        )

    return catalog, draft
