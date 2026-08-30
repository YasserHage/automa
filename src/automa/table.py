"""What is actually on the table, read off it by the person sitting there.

The other half of the informant seam. :mod:`automa.catalog.informant` answers what a
card *does*; this answers *which* cards there are - the hand that was just dealt, the
card that was just drawn, the face the die showed. Both are facts only the table has,
and both travel the same way: the module declares an ``unknown``, the core relays it, the
answer comes back to ``resolve_unknown``.

This is what makes a match a game rather than a simulation of one. Without it a module
deals from its own shuffled deck and Automa plays a hand nobody at the table is holding.

**No model is involved and none should be.** A card's identity is a fact, not prose that
needs structuring: the person types what they see, this splits it and counts it, and the
module gives the tokens meaning. Sending them past a model could only introduce error,
and would spend a call on every deal.

**The only validation is the count.** The module says how many components it expects and
a mismatch is put back to the person; nothing else is checked. In particular a name the
module has never heard of is *not* rejected - for a game whose cards are learned it flows
straight on into the ``card_effect`` path and becomes a card described once and
remembered. Refusing unfamiliar names would break precisely the unfamiliar-game case
this product exists for.

Not answering is always allowed. An empty line means "I cannot say", which the module
answers with its own seeded deal - the same fallback self-play runs on.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from automa.core.game import (
    COMPONENTS,
    COUNT,
    KIND_COMPONENTS,
    OPTIONS,
    identifying,
    unavailable_answer,
)

#: Attempts before a question is given up on. A person who has miscounted three times is
#: better served by playing on with the module's own deal than by being asked a fourth.
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Question:
    """One question about the components, as it is put to a person.

    Carries what the module asked for rather than a pre-formatted string, so an
    interface can lay it out however it likes - and so a test can answer one without
    pretending to be a terminal.
    """

    prompt: str
    count: int | None = None
    options: tuple[str, ...] = ()
    note: str | None = None
    """Why the previous answer could not be used. ``None`` on the first attempt."""


#: How a components question is put to somebody. Returns their line, or ``None`` for
#: "I cannot say" - which is always a permitted answer.
ComponentAsker = Callable[[Question], str | None]


def never_asks(_question: Question) -> None:
    """A :data:`ComponentAsker` for unattended runs: nothing is ever supplied."""
    return None


def split_components(text: str) -> list[str]:
    """The person's line, as tokens.

    Commas win where they appear, because a component's name can contain a space -
    ``salmon nigiri, maki 2`` is two cards and splitting it on whitespace would make
    four. Where there is no comma, whitespace separates, which is what anybody typing
    ``1/5 2/9 3/4`` expects.

    Normalisation is cosmetic and deliberately shallow: case folded, surrounding
    whitespace dropped, internal runs of whitespace turned into single underscores so
    ``Salmon Nigiri`` arrives as the ``salmon_nigiri`` a module would call it. Nothing is
    mapped, corrected or discarded - what the person typed is what the module gets.
    """
    parts = text.split(",") if "," in text else re.split(r"\s+", text.strip())
    return [re.sub(r"\s+", "_", p.strip().lower()) for p in parts if p.strip()]


def table_informant(ask: ComponentAsker = never_asks, *, attempts: int = MAX_ATTEMPTS):
    """Build an informant that answers :data:`KIND_COMPONENTS` questions by asking ``ask``.

    Any other kind of question is answered ``unavailable`` rather than wrongly, so this
    composes with the catalog informant instead of competing with it - see
    :func:`automa.core.match.compose`.
    """

    def informant(_ctx, unknown: dict[str, Any]) -> dict[str, Any]:
        if unknown.get("kind") != KIND_COMPONENTS:
            return unavailable_answer(unknown)

        wanted = unknown.get(COUNT)
        options = unknown.get(OPTIONS) or ()
        prompt = unknown.get("prompt") or "What is on the table?"
        note: str | None = None

        for _ in range(max(1, attempts)):
            answer = ask(
                Question(
                    prompt=prompt,
                    count=wanted if isinstance(wanted, int) else None,
                    options=tuple(options),
                    note=note,
                )
            )
            if answer is None or not answer.strip():
                # Not a failure: nobody could say, and the module deals its own.
                return unavailable_answer(unknown)

            components = split_components(answer)
            if isinstance(wanted, int) and wanted >= 0 and len(components) != wanted:
                note = (
                    f"that was {len(components)} component(s), and {wanted} "
                    f"{'was' if wanted == 1 else 'were'} expected"
                )
                continue
            return {**identifying(unknown), COMPONENTS: components}

        return unavailable_answer(unknown)

    return informant
