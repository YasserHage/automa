"""The Cataloguer: a plain description of a card, turned into executable data.

The third agent in the project, and the one with the narrowest remit. The Tactician picks
an index; the Compiler writes a module a human then approves; this one takes a sentence a
person said at the table - *"tempura, you need two of them and they're worth five"* - and
emits a list of typed operations from a closed vocabulary.

It cannot invent behaviour. Its output is validated against
:mod:`automa.catalog.vocabulary` before it is stored, and a description that will not fit
is **parked for a person** rather than approximated. That refusal is the point: the
alternative is a model quietly deciding what a card does, which is exactly the failure
this architecture exists to prevent, and it would make invariant 5 a slogan rather than a
property.

`call_llm` retries a *malformed* answer with the validation errors fed back, so a fixable
mistake gets fixed. An unfixable one - a card whose behaviour the vocabulary genuinely
cannot express - exhausts the attempts and reaches the escape case, which is the correct
outcome rather than a bug.
"""

from __future__ import annotations

from dataclasses import dataclass

from automa.catalog.vocabulary import (
    describe_terms,
    describe_vocabulary,
    unsupported,
    validate,
)
from automa.contracts import CardEffects
from automa.llm import call_llm
from automa.llm.call import LLMCallError, LLMResult
from automa.trajectory import TrajectoryWriter

SYSTEM = """\
You translate a plain-language description of a game card into a fixed set of \
operations. You are not deciding what the card does - the person describing it already \
knows - you are only expressing what they said in the available vocabulary.

**Not every card scores.** A card that changes what its owner may *do* - take two cards \
instead of one, ignore a restriction, act out of order - is expressed with `set_flag`, \
which puts a named marker on its owner that the game's own rules already test. The rules \
of play were compiled separately and are not your concern; your job is only to record \
that the card raises the marker. When a description sounds like a rule rather than a \
score, `set_flag` is usually what it wants, named after the card or the permission it \
grants.

The vocabulary is closed. If the description genuinely cannot be expressed in it, say so \
in `cannot_express` and return no operations. Do not approximate, do not pick the nearest \
operation, and do not invent a new one. A card recorded as doing the wrong thing is far \
worse than a card recorded as not yet understood: the first plays a silently incorrect \
game, the second asks a person for help.

But do not reach for `cannot_express` simply because a card has no points on it. Doing \
that leaves the module's own rule for the card switched off, which plays a silently \
incorrect game just as surely as a wrong number would.\
"""


@dataclass
class CataloguedCard:
    """The result of structuring one description."""

    card_type: str
    description: str
    effects: list[dict] | None = None
    unresolved: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.effects) and not self.unresolved


def _validator(supported: set[str] | None, terms: dict[str, set[str]] | None = None):
    """Reject operations that will not execute, so `call_llm` retries with the reason.

    Two ways an answer fails to execute, and both are formatting rather than judgement:
    an operation that is not in the vocabulary at all, and one that is but which *this
    game* does not implement. Neither is a reason to guess.

    A model saying "I cannot express this" is accepted immediately - that is a valid
    answer, and retrying it would only pressure the model into inventing something.
    """

    def _validate(result: LLMResult) -> None:
        parsed: CardEffects = result.parsed
        if parsed.cannot_express:
            return
        effects = [e.model_dump(exclude_none=True) for e in parsed.effects]
        problems = validate(effects) + unsupported(effects, supported, terms)
        if problems:
            raise ValueError(
                "those operations will not execute: "
                + "; ".join(problems[:4])
                + ". Correct them, or set cannot_express if the card genuinely does "
                "not fit."
            )

    return _validate


def catalogue_card(
    card_type: str,
    description: str,
    *,
    game: str = "",
    supported: set[str] | None = None,
    terms: dict[str, set[str]] | None = None,
    trajectory: TrajectoryWriter | None = None,
    max_attempts: int = 3,
) -> CataloguedCard:
    """Structure one card description into vocabulary operations.

    Args:
        supported: The operations this game's module actually interprets. A vocabulary
            broad enough for many games is wider than any one game implements, and
            storing an effect the module will ignore is worse than storing none - the
            card would look catalogued and quietly do nothing. ``None`` means the
            module did not declare and nothing is restricted.
        terms: The names this game's module actually looks for, per vocabulary field
            - ``{"flag": {"chopsticks"}, "count_field": {"icons"}}``. These are
            the values the catalog and the module must agree on, and a disagreement
            never raises: the module looks for a name nobody wrote and its rule
            quietly never fires.

    Never raises for an un-expressible card: it comes back with ``unresolved`` set, and
    the caller parks it. The only thing that fails here is the provider.
    """
    prompt = (
        f"{describe_vocabulary(supported)}\n\n"
        f"{describe_terms(terms, card_type)}\n\n"
        f"Card: {card_type}\n"
        f"Description given by the player: {description}\n\n"
        "Express this card as operations, or set cannot_express if it does not fit."
    )
    try:
        result = call_llm(
            agent="cataloguer",
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=CardEffects,
            validate=_validator(supported, terms),
            max_attempts=max_attempts,
            max_tokens=2048,
            trajectory=trajectory,
            tags={"game": game, "card": card_type},
        )
    except LLMCallError as exc:
        # Attempts exhausted. Either the provider is down or the vocabulary genuinely
        # cannot hold this card; both mean a person has to look, and neither is a
        # licence to guess.
        return CataloguedCard(
            card_type=card_type,
            description=description,
            unresolved=f"could not be expressed in the vocabulary: {exc}",
        )

    parsed: CardEffects = result.parsed
    if parsed.cannot_express:
        return CataloguedCard(
            card_type=card_type,
            description=description,
            unresolved=parsed.cannot_express,
        )
    return CataloguedCard(
        card_type=card_type,
        description=description,
        effects=[e.model_dump(exclude_none=True) for e in parsed.effects],
    )
