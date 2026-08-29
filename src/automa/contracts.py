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
    """One turn's decision (plan.md: "returns an index and a one-line justification").

    ``action_index`` points into the numbered list of legal actions the core
    enumerated for this turn. The model never names an action, so an action that
    does not exist cannot be expressed in this type at all - that is invariant 1
    showing up in the type system.

    The bound on the upper end depends on how many actions were legal this turn, so
    it cannot live in the schema; the caller passes a ``validate`` callback to
    :func:`automa.llm.call_llm` which rejects an out-of-range index and retries with
    the reason fed back.
    """

    action_index: int = Field(
        ge=0,
        description="Index of the chosen action, from the numbered list of legal actions.",
    )
    justification: str = Field(
        min_length=1,
        max_length=300,
        description="One line explaining the choice, in terms a player would understand.",
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
