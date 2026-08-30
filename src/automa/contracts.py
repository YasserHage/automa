"""Typed contracts between the deterministic core and the model agents.

Every agent's output shape lives here, in one file, so the entire surface where a
model is allowed to influence the game is reviewable at a glance. That surface is
deliberately narrow: the Tactician returns an *index*, never an action; the
Strategist returns *weights*, never a move; the Compiler returns *source text* that
is validated before it is ever imported.

This module imports nothing but Pydantic - no provider SDK, no :mod:`automa.llm` -
so :mod:`automa.core` may depend on it without breaching invariants 1 and 2.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TacticianResponse(BaseModel):
    """One turn's decision: an index into the legal actions, and nothing else.

    ``action_index`` points into the numbered list of legal actions the core
    enumerated for this turn. The model never names an action, so an action that
    does not exist cannot be expressed in this type at all - that is invariant 1
    showing up in the type system.

    The bound on the upper end depends on how many actions were legal this turn, so
    it cannot live in the schema; the caller passes a ``validate`` callback to
    :func:`automa.llm.call_llm` which rejects an out-of-range index and retries with
    the reason fed back.

    **`plan.md` specifies "an index and a one-line justification"; the justification was
    removed by decision after it was measured.** Across 382 real decisions the median
    response was 39 output tokens and roughly 20 of them were that line - half the output
    of every turn, on a tier where latency is the binding constraint. It also bought
    nothing: nothing downstream read it, and Phase 3 found the lines confidently wrong
    often enough that they were closer to a misleading artifact than a record of
    reasoning. What a decision was made from is still fully recoverable - the trajectory
    holds the whole prompt, the numbered actions and the index chosen.

    :class:`BaselineMove` deliberately keeps its justification. It names a move instead of
    choosing an index, and its Phase 3 measurements are already recorded and compared.
    """

    action_index: int = Field(
        ge=0,
        description="Index of the chosen action, from the numbered list of legal actions.",
    )


class Priority(BaseModel):
    """A single weighted intent inside a policy."""

    description: str = Field(
        min_length=1,
        max_length=200,
        description="What to favour, e.g. 'deny the opponent blue resources'.",
    )
    weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Relative importance, 0 to 1, against the other priorities.",
    )


class StrategistPolicy(BaseModel):
    """A policy, not a move plan (plan.md: "priorities and weights").

    The Strategist runs on the slow cycle and its output is meant to stay stable
    across many turns, so it must not encode anything turn-specific. ``rationale``
    is what strategic memory keeps when a policy is later replaced, so the
    trajectory can show not just which policy was active but why it changed.
    """

    summary: str = Field(
        min_length=1,
        max_length=300,
        description="The policy in one line, suitable for showing to the human player.",
    )
    priorities: list[Priority] = Field(
        min_length=1,
        max_length=6,
        description="Weighted priorities, most important first.",
    )
    rationale: str = Field(
        min_length=1,
        description="Why this policy suits this match. Retained in strategic memory.",
    )


class CompilerOutput(BaseModel):
    """A compiled game module plus what the Compiler wants a human to check.

    The architecture concentrates error in compilation precisely so that it is
    auditable, which only pays off if the Compiler is made to *say* where it was
    unsure. ``assumptions`` and ``open_questions`` are that admission: they are
    surfaced alongside ``plain_language_summary`` in the approval step before any
    match begins.

    ``module_source`` is text, not an imported module. It is validated by random
    self-play in a subprocess before it is trusted (plan.md Phase 4). The card
    catalog is a separate, persistent artefact per plan.md Phase 5 and is not part
    of this output.
    """

    module_source: str = Field(
        min_length=1,
        description="Python source implementing the game module interface.",
    )
    plain_language_summary: str = Field(
        min_length=1,
        description="What the Compiler understood the rules to be. Shown for approval.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Choices made where the rule material was ambiguous or silent.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Points the Compiler could not resolve and wants a human to settle.",
    )


class BaselineMove(BaseModel):
    """One move from the Phase 3 baseline, which plays without enumeration.

    The baseline never receives a list of legal actions - that is the whole
    difference under test - so it cannot answer with an index. It answers with the
    action itself, as a JSON object in the game's own action shape, and the harness
    decides afterwards whether that action was legal.

    ``action_json`` is a *string* rather than a nested model for two reasons. Action
    shapes are game-specific, so no single schema covers them; and a plain string is
    the one field type every provider handles identically, which matters because the
    baseline must never lose on formatting. Malformed JSON is retried through
    :func:`automa.llm.call_llm`'s feedback loop; an illegal *move* is never retried,
    because being told a move is illegal is precisely the corrective intervention the
    primary metric counts.
    """

    action_json: str = Field(
        min_length=1,
        description=(
            "The chosen action as a JSON object, using the exact keys shown in the "
            "rules brief. Example: {\"kind\": \"take\", \"area\": 0}"
        ),
    )
    justification: str = Field(
        min_length=1,
        max_length=300,
        description="One line explaining the choice, in terms a player would understand.",
    )


class EffectOp(BaseModel):
    """One operation from the closed effect vocabulary.

    Every field a vocabulary operation can take, all optional, because the operations
    differ in shape and a provider needs one concrete schema. Which fields are actually
    required is decided by :func:`automa.catalog.vocabulary.validate`, not here - the
    vocabulary is the authority, and duplicating its rules in a Pydantic model would
    give two places to disagree.

    The one real coupling in the design: adding an operation whose parameters are not
    already listed here means adding a field. `tests/test_catalog.py` asserts the two
    stay in step, so the omission surfaces as a failing test rather than as a model
    unable to express something the vocabulary claims to offer.
    """

    op: str = Field(min_length=1, description="Operation name, from the given list.")
    # Scoring
    points: int | None = Field(default=None, description="Points scored.")
    size: int | None = Field(default=None, description="Set size that scores.")
    tiers: list[int] | None = Field(default=None, description="Cumulative totals by count.")
    first: int | None = Field(default=None, description="Points for having the most.")
    second: int | None = Field(default=None, description="Points for second most.")
    last: int | None = Field(default=None, description="Points (often negative) for fewest.")
    count_field: str | None = Field(default=None, description="What is counted, if not copies.")
    # Resources
    resource: str | None = Field(default=None, description="Named resource gained or spent.")
    amount: int | None = Field(default=None, description="How much of it.")
    per: int | None = Field(default=None, description="Units of a resource per scoring step.")
    # Cards and conditions
    count: int | None = Field(default=None, description="Number of cards drawn or discarded.")
    flag: str | None = Field(default=None, description="Persistent marker set on its owner.")
    category: str | None = Field(default=None, description="Card category affected.")
    factor: int | None = Field(default=None, description="Multiplier applied.")


class CardEffects(BaseModel):
    """What the Cataloguer returns for one described card.

    ``cannot_express`` is the important field. The vocabulary is closed, and a card that
    does not fit it must be *refused* rather than approximated - Sushi Go's chopsticks
    changes how many cards you take on a turn, which no scoring operation can say. A
    model that guessed there would silently make the system play a different game, so
    the contract gives it an explicit way to decline.
    """

    effects: list[EffectOp] = Field(
        default_factory=list,
        description="The card's effect as operations. Empty if it cannot be expressed.",
    )
    cannot_express: str = Field(
        default="",
        description=(
            "Set this, and leave effects empty, when the description cannot be "
            "expressed in the vocabulary. Say briefly what the card does that no "
            "operation covers."
        ),
    )
    notes: str = Field(
        default="",
        description="Anything a human reviewing the catalog should know.",
    )



class DraftedCard(BaseModel):
    """One card the catalog builder found described in the rule material.

    Distinct from :class:`CardEffects` in what it carries rather than what it means:
    a person describing a card at the table is answering a question about that card,
    so the card type is already known. A model reading a whole rulebook has to say
    which card it is talking about, and where it read it.
    """

    card_type: str = Field(
        min_length=1,
        description=(
            "The card type exactly as the submitted card list names it. If the "
            "rulebook and the card list disagree, follow the card list - the module "
            "was compiled against those names and will look effects up by them."
        ),
    )
    effects: list[EffectOp] = Field(
        default_factory=list,
        description="Its effect as operations. Empty if it cannot be expressed.",
    )
    cannot_express: str = Field(
        default="",
        description=(
            "Set this, and leave effects empty, when the material does describe this "
            "card but the vocabulary cannot say what it does."
        ),
    )
    evidence: str = Field(
        default="",
        description=(
            "The phrase in the material this was read from, so a person can check it "
            "without re-reading the rulebook."
        ),
    )


class CatalogDraft(BaseModel):
    """Every card effect a model could find in submitted rule material.

    Only cards the material actually describes belong here. A card named in the deck
    list but never explained must simply be **left out** - not guessed at, and not
    reported as un-expressible. The two outcomes are different and are stored
    differently: a card that defeated the vocabulary is parked and never asked about
    again, whereas a card nobody has described yet is exactly what the table is for.
    Reporting the second as the first would silently make it unaskable.
    """

    cards: list[DraftedCard] = Field(
        default_factory=list,
        description="One entry per card the material describes. Omit the rest.",
    )
    not_described: list[str] = Field(
        default_factory=list,
        description=(
            "Card types you saw named but found no effect for. These are left for a "
            "person to describe at the table; listing them here is a note, not a "
            "verdict."
        ),
    )
    notes: str = Field(
        default="",
        description="Anything a person reviewing the drafted catalog should know.",
    )
