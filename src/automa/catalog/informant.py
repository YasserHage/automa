"""The table's answer to a question the module cannot answer itself.

A game whose card effects are learned declares what it does not know and stops. Something
has to answer, and that something is an :data:`~automa.core.match.Informant` - a function
the core calls with the question, which returns a fact. Never a decision: the person is
supplying information the module lacks, not choosing a move, which is why it is logged as
``table_information`` and never counted as a corrective intervention.

**The catalog is consulted before the person is.** That is the whole of incremental
ingestion, and it is why no catalog ever has to be loaded into a module's state: the
module asks about sashimi every single match, and from the second match onwards the
answer comes back from the store without anybody being disturbed. What shrinks between
matches is the number of questions that reach a human, which is exactly what the phase
claims and exactly what it measures.

Three ways a question is answered, in order:

1. the catalog knows the card - answer from it, ask nobody;
2. the catalog has it parked - answer ``unavailable``, ask nobody, because being asked a
   second time about a card that already defeated the vocabulary is pure noise;
3. nobody has ever described it - put it to the person, structure their words into
   operations with the cataloguer agent, store the result, and answer.

The person's words become *data* on that third path and never behaviour: the cataloguer
returns operations that must validate against the closed vocabulary, and a description
that will not fit is parked rather than approximated.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from automa.agents.cataloguer import catalogue_card
from automa.catalog.store import Catalog, terms_for
from automa.core.game import UNAVAILABLE
from automa.trajectory import TrajectoryWriter

#: What a question is put to a person with. Takes the module's prompt, returns their
#: words - or ``None`` for "I cannot say", which is always a permitted answer.
Asker = Callable[[str], str | None]

#: The kind of question this informant knows how to answer. A module asking anything
#: else gets ``unavailable`` rather than a wrong answer.
CARD_EFFECT = "card_effect"


def unavailable(card_type: str) -> dict[str, Any]:
    """The answer that is always available: nobody could supply this fact."""
    return {"card_type": card_type, UNAVAILABLE: True}


def never_asks(_prompt: str) -> None:
    """An :data:`Asker` for unattended runs. Every question goes unanswered.

    Not a stub for testing - it is what an automated match legitimately does when there
    is no person at the table, and a module that cannot finish under it was never fit to
    be played.
    """
    return None


def catalog_informant(
    catalog: Catalog,
    ask: Asker = never_asks,
    *,
    trajectory: TrajectoryWriter | None = None,
    save: bool = True,
    on_learn: Callable[[str, bool], None] | None = None,
):
    """Build an informant that answers from ``catalog``, asking ``ask`` only when it must.

    Args:
        catalog: Consulted first and written back to. Persisted after each new card so
            an interrupted match still leaves behind what it learned - the questions a
            person already answered are the expensive part, and losing them to a crash
            would make every later match ask again.
        ask: How a genuinely new card is put to the person.
        save: Whether to write the catalog to disk as it grows. Off for tests.
        on_learn: Called with ``(card_type, was_stored)`` after each new description, so
            a caller can report progress without this module doing terminal I/O.
    """
    supported = None
    terms = None

    def note(card_type: str, source: str, **extra) -> None:
        """Record which of the three paths answered, because that is the measurement.

        `table_information` alone cannot say: an answer read from the catalog and an
        answer typed by a person arrive in identical shape. Phase 5's whole claim is a
        count of the second kind, so without this the headline number is unauditable
        from the record - it has to be taken on trust, which for a submitted result is
        no better than not measuring it.
        """
        if trajectory is not None:
            trajectory.emit(
                "core_event",
                event="catalog_answer",
                game=catalog.game,
                card_type=card_type,
                source=source,
                asked_a_person=source in ("described", "declined", "parked_after_asking"),
                **extra,
            )

    def informant(ctx, unknown: dict[str, Any]) -> dict[str, Any]:
        nonlocal supported, terms
        if supported is None:
            # Read off the module rather than passed in: what a game implements is the
            # game's to declare, and a caller repeating it could get it wrong.
            declared = getattr(ctx.game, "SUPPORTED_OPS", None)
            supported = set(declared) if declared is not None else None
            terms = terms_for(ctx.game)

        if unknown.get("kind") != CARD_EFFECT:
            return {**unknown, UNAVAILABLE: True}

        card_type = unknown.get("card_type")
        if not card_type:
            return {UNAVAILABLE: True}

        # 1 and 2: already settled, one way or the other.
        entry = catalog.entries.get(card_type)
        if entry is not None:
            if entry.playable:
                note(card_type, "catalog")
                return {"card_type": card_type, "effects": entry.effects}
            note(card_type, "catalog_parked")
            return unavailable(card_type)

        # 3: nobody has ever described this one.
        prompt = unknown.get("prompt") or f"What does {card_type} do?"
        words = ask(prompt)
        if not words or not words.strip():
            # Declining to answer still counts as having been asked. Parking it is what
            # stops the same question arriving on every future turn and every future
            # match - the person said they could not say, and that is an answer.
            catalog.park(card_type, "", "nobody at the table could describe it")
            note(card_type, "declined")
            if save:
                catalog.save()
            if on_learn:
                on_learn(card_type, False)
            return unavailable(card_type)

        card = catalogue_card(
            card_type,
            words.strip(),
            game=catalog.game,
            supported=supported,
            terms=terms,
            trajectory=trajectory,
        )

        if card.effects:
            problems = catalog.learn(
                card_type,
                effects=card.effects,
                description=card.description,
                supported=supported,
                terms=terms,
            )
            if not problems:
                note(card_type, "described", effects=card.effects)
                if save:
                    catalog.save()
                if on_learn:
                    on_learn(card_type, True)
                return {"card_type": card_type, "effects": card.effects}
            # Validated once inside the agent and refused here: the two disagree, so the
            # card is parked rather than stored on a second opinion.
            catalog.park(card_type, card.description, "; ".join(problems))
            note(card_type, "parked_after_asking", reason="; ".join(problems))
        else:
            catalog.park(card_type, card.description, card.unresolved or "did not fit")
            note(card_type, "parked_after_asking", reason=card.unresolved or "did not fit")

        if save:
            catalog.save()
        if on_learn:
            on_learn(card_type, False)
        return unavailable(card_type)

    return informant
