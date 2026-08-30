"""The effect vocabulary: what a card is allowed to do, as data.

Invariant 5 says *card effects are executable data structures, never prose interpreted
at runtime*. This module is that invariant made concrete. A card's effect is a list of
typed operations drawn from a fixed set; a game module reads those operations and acts on
them. Nothing anywhere evaluates a sentence, and no model ever emits behaviour - only
data that has to validate against the shapes below.

The vocabulary is deliberately **closed**. A description that cannot be expressed in it is
parked and put to a person, rather than accommodated by inventing a new primitive at match
time - which is the one thing this architecture will not let a model do.

**Scope.** These primitives are drawn from the categories `plan.md` names - *"gain
resource, force discard, activate on condition, score by set"* - not from whichever game
happens to be implemented. An earlier draft contained only scoring operations, which was
enough for Sushi Go and could not express a single card from the resource game sketched in
Phase 1. A vocabulary shaped by one game is not a vocabulary.

**A module need not implement all of them.** Sushi Go has no resources and never forces a
discard. :func:`unsupported` is how that is caught: a module declares the operations it
interprets, and a card needing one it does not gets parked rather than silently ignored.
An ignored operation is the worst outcome available - the card looks catalogued and does
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Op:
    """One primitive: its name, the fields it needs, and what it means."""

    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    summary: str = ""

    def describe(self) -> str:
        fields = ", ".join(self.required)
        extra = f" (optional: {', '.join(self.optional)})" if self.optional else ""
        return f"{self.name}({fields}){extra} - {self.summary}"


#: The closed set. Adding to this is a deliberate act by a person editing this file, never
#: something that can happen during a match.
VOCABULARY: dict[str, Op] = {
    op.name: op
    for op in (
        # -- scoring ------------------------------------------------------
        Op("score_flat", ("points",),
           summary="Score a fixed number of points for each copy collected."),
        Op("score_per_set", ("size", "points"),
           summary=("Score points for each complete set of `size` copies; an incomplete "
                    "set scores nothing.")),
        Op("score_tiered", ("tiers",),
           summary=("Score by how many were collected, from a lookup: the 1st is worth "
                    "tiers[0], the 2nd brings the total to tiers[1], and so on.")),
        Op("score_majority", ("first",), ("second", "count_field"),
           summary=("Compare a total against the opponent when scoring: most scores "
                    "`first`, second-most scores `second`, ties split.")),
        Op("score_at_end", ("first",), ("last", "count_field"),
           summary=("Like score_majority but resolved once at the very end of the game "
                    "rather than each round.")),
        Op("score_per_resource", ("resource", "per", "points"),
           summary=("Score `points` for every `per` units of a resource still held at "
                    "the end.")),
        # -- resources ----------------------------------------------------
        Op("gain", ("resource", "amount"),
           summary="Gain an amount of a named resource."),
        Op("spend", ("resource", "amount"),
           summary=("Pay an amount of a named resource. A card that cannot be paid for "
                    "is not legal to play.")),
        # -- cards --------------------------------------------------------
        Op("draw", ("count",),
           summary="Draw a number of cards."),
        Op("discard_opponent", ("count",),
           summary="Force the opponent to discard a number of cards."),
        # -- conditions ---------------------------------------------------
        Op("set_flag", ("flag",),
           summary=("Set a persistent marker on its owner, which another card or rule "
                    "can test - a shield, a status, a one-shot protection.")),
        Op("multiply_next", ("category", "factor"),
           summary=("The next card of `category` collected is worth `factor` times its "
                    "value.")),
    )
}


class VocabularyError(ValueError):
    """A described effect cannot be expressed in the vocabulary."""


def validate(effects: list[dict[str, Any]]) -> list[str]:
    """Problems with a list of ops. Empty means it is well-formed.

    Returned as readable strings rather than raised: they go back to the structuring
    agent as repair feedback, and to a person when the escape case fires.
    """
    problems: list[str] = []
    if not isinstance(effects, list):
        return [f"effects must be a list of operations, got {type(effects).__name__}"]
    if not effects:
        return ["effects is empty; a card with no effect should say so explicitly"]

    for i, effect in enumerate(effects):
        if not isinstance(effect, dict):
            problems.append(f"[{i}] must be an object, got {type(effect).__name__}")
            continue
        name = effect.get("op")
        op = VOCABULARY.get(name)
        if op is None:
            problems.append(
                f"[{i}] unknown operation {name!r}. The vocabulary is closed; "
                f"available operations are {sorted(VOCABULARY)}."
            )
            continue
        for field in op.required:
            if field not in effect:
                problems.append(f"[{i}] {name} requires {field!r}")
        allowed = {"op", *op.required, *op.optional}
        for field in effect:
            if field not in allowed:
                problems.append(
                    f"[{i}] {name} has no field {field!r}; it takes {sorted(allowed)}"
                )
    return problems


#: Vocabulary fields whose *value* is a name rather than a number - a quantity the module
#: totals, a marker its rules test, a resource it tracks, a group of cards it recognises.
#:
#: These are the only places where a catalog and a module can disagree while both remain
#: internally valid, and the disagreement never raises: the module simply looks for a name
#: nobody wrote and its rule quietly never fires. Two such bugs shipped before this was
#: generalised - maki counting card copies instead of icons, and Sushi Go's chopsticks
#: mechanic switched off because the flag was called `extra_card_from_hand`.
NAMED_FIELDS: tuple[str, ...] = ("count_field", "flag", "resource", "category")


def unsupported(
    effects: list[dict[str, Any]],
    supported: set[str] | None,
    terms: dict[str, set[str]] | None = None,
) -> list[str]:
    """What this game's module will not act on: an operation, or a name it never uses.

    A vocabulary wide enough to describe many games is necessarily wider than any one
    game implements: Sushi Go has no resources and never forces a discard. Storing a card
    whose effect the module will not act on is the worst available outcome - the card
    looks catalogued and quietly does nothing - so it is parked instead, down the same
    path as a card the vocabulary cannot express at all.

    :data:`NAMED_FIELDS` get the same treatment for a different reason. Their values are
    strings both sides must agree on, and a mismatch is neither a broken catalog nor a
    broken module - it is the two disagreeing, silently, while still producing a
    plausible game. That is worse than either being wrong alone, because nothing looks
    like an error.

    Args:
        supported: Operations the module interprets, or ``None`` if it did not declare.
        terms: Per-field names the module recognises, e.g.
            ``{"flag": {"chopsticks"}, "count_field": {"icons"}}``. A field absent from
            the mapping is unchecked; ``None`` checks nothing at all.
    """
    problems: list[str] = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        op = effect.get("op")
        if supported is not None and op not in supported:
            problems.append(
                f"{op!r} is in the vocabulary but {sorted(supported)} is "
                "all this game implements"
            )
        if not terms:
            continue
        for field in NAMED_FIELDS:
            value = effect.get(field)
            known = terms.get(field)
            if value is None or known is None:
                continue
            if value not in known:
                problems.append(
                    f"{op!r} names {field}={value!r}, which this game never uses; "
                    f"it knows {sorted(known)}"
                )
    return problems


def describe_terms(terms: dict[str, set[str]] | None, card_type: str = "") -> str:
    """The names this game recognises, as prompt text for a structuring agent.

    Without this an agent is asked to invent a name with nothing to invent it from, and
    any plausible string validates. The fallback convention is doing real work: a module
    that gates a rule on a card names the marker after that card, so defaulting to the
    card's own name makes both sides agree even where the module declares nothing.
    """
    lines: list[str] = []
    for field in NAMED_FIELDS:
        known = (terms or {}).get(field)
        if known:
            lines.append(f"- `{field}`: use one of exactly {', '.join(sorted(known))}.")
        elif known is not None:
            lines.append(f"- `{field}`: this game uses none; leave the field out.")

    unknown = [f for f in NAMED_FIELDS if (terms or {}).get(f) is None]
    if unknown:
        named = ", ".join(f"`{f}`" for f in unknown)
        default = f"the card's own type ({card_type})" if card_type else "the card's own type"
        lines.append(
            f"- {named}: this game has not declared which names it uses, so these cannot "
            f"be checked. Name a marker or category after {default} unless the material "
            "clearly gives it another name, and omit the field entirely if the card does "
            "not need it."
        )

    return "The names used inside operations must match what the game looks for:\n" + "\n".join(
        lines
    )


def describe_vocabulary(supported: set[str] | None = None) -> str:
    """The vocabulary as prompt text, generated from the definitions themselves.

    Written from :data:`VOCABULARY` rather than restated in a prompt string, so what the
    structuring agent is told can never drift from what :func:`validate` accepts - the
    same reason the compiler reads its interface out of `core/game.py`.

    When a module declares what it supports, only those operations are offered. Showing
    a model a primitive the game cannot act on invites a plausible answer that would
    silently do nothing.
    """
    names = sorted(supported) if supported else sorted(VOCABULARY)
    lines = ["Available operations (this list is complete - nothing else is accepted):", ""]
    lines += [f"- {VOCABULARY[n].describe()}" for n in names if n in VOCABULARY]
    return "\n".join(lines)
