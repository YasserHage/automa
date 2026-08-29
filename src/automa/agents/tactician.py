"""The Tactician: the fast cycle, one decision per turn.

It receives the board as Automa may see it, the active policy when there is one, and
a numbered list of legal actions. It returns an index and one line of justification.
That is the entire surface through which a model touches the game.

Everything that makes an illegal move impossible lives outside this file. The core
built the list; this agent picks a position in it; the core applies the result. The
worst a confused model can do here is choose a legal action badly - which is a
quality problem, measurable in Phase 3, not a correctness one.

This module imports :mod:`automa.llm`, which is exactly why it lives in ``agents/``
and not in ``core/``.
"""

from __future__ import annotations

import random

from automa.contracts import TacticianResponse
from automa.core.match import Decision, TurnContext
from automa.llm import call_llm
from automa.llm.call import LLMCallError, LLMResult

SYSTEM = """\
You are Automa, playing a physical board or card game against a human opponent who is \
sitting at the table with the real components.

Every turn you are given the state of the game as you are permitted to see it, and a \
numbered list of the legal actions available to you. That list is complete and it is \
already known to be legal - it was computed from the rules, not by you. You therefore \
never need to reason about whether a move is allowed, and you must never propose one \
that is not on the list.

Choose exactly one action by its index, and give one short line explaining why, in \
terms a player at the table would understand. Play to win.\
"""


def tactician_decider(policy: str | None = None, *, max_attempts: int = 3):
    """Build a :data:`~automa.core.match.Decider` backed by a model.

    Args:
        policy: The active strategic policy, when a Strategist has produced one.
            Phase 6 fills this; while it is ``None`` the prompt omits the section
            entirely rather than telling the model it has no strategy.
        max_attempts: Passed to :func:`call_llm`, which retries an out-of-range index
            with the reason fed back to the model.
    """

    def decide(ctx: TurnContext) -> Decision:
        prompt = _prompt(ctx, policy)
        try:
            result = call_llm(
                agent="tactician",
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_format=TacticianResponse,
                validate=_only_legal(len(ctx.actions)),
                max_attempts=max_attempts,
                # Generous on purpose: on Gemini, thinking tokens come out of this
                # same budget, so a tight cap can be spent reasoning and return no
                # answer at all.
                max_tokens=2048,
                trajectory=ctx.trajectory,
                tags={"game": ctx.game.NAME, "turn": ctx.turn, "ply": ctx.ply},
            )
        except LLMCallError as exc:
            return _fallback(ctx, str(exc))

        return Decision(
            index=result.parsed.action_index,
            justification=result.parsed.justification,
        )

    return decide


def _prompt(ctx: TurnContext, policy: str | None) -> str:
    parts = [ctx.view()]
    if policy:
        parts.append(f"Your strategy for this match:\n{policy}")
    parts.append(f"Legal actions:\n{ctx.numbered()}")
    parts.append("Choose one by its index.")
    return "\n\n".join(parts)


def _only_legal(count: int):
    """Reject an index outside the enumerated list.

    This is what turns a hallucinated action into a logged retry instead of a bad
    move: :func:`call_llm` feeds the rejection back and asks again, and both attempts
    land in the trajectory.
    """

    def _validate(result: LLMResult) -> None:
        index = result.parsed.action_index
        if not 0 <= index < count:
            raise ValueError(
                f"action_index {index} is outside the legal range 0..{count - 1}. "
                f"There are {count} legal actions this turn."
            )

    return _validate


def _fallback(ctx: TurnContext, error: str) -> Decision:
    """Play a random legal action when the model could not be reached.

    A provider outage should not end a match that is otherwise sound, and it should
    certainly not read as a rules failure. So the match continues on a random legal
    action - still enumerated by the core, so still legal - and the substitution is
    recorded prominently rather than passed off as a decision. ``MatchResult``
    counts these separately so Phase 3 can decide how they score.
    """
    if ctx.trajectory is not None:
        ctx.trajectory.emit(
            "core_event",
            event="tactician_fallback",
            ply=ctx.ply,
            turn=ctx.turn,
            error=error,
            note="Model unreachable; played a random legal action to keep the match alive.",
        )
    index = random.Random(f"fallback-{ctx.game.NAME}-{ctx.ply}").randrange(len(ctx.actions))
    return Decision(
        index=index,
        justification="(fallback: the model could not be reached this turn)",
        fallback=True,
    )
