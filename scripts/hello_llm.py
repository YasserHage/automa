"""Phase 0 exit criterion: one real LLM call, one readable trajectory.

    python scripts/hello_llm.py

Proves the spine end to end - the wrapper reaches a real model, the response is
structured, and the trajectory comes back with cost and latency filled in. It also
exercises the validation-retry path on purpose, so the very first trajectory in the
repository already shows a rejection and a corrected second attempt.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

import config  # noqa: E402
from llm import call_llm  # noqa: E402
from trajectory import TrajectoryWriter  # noqa: E402


class Choice(BaseModel):
    """Deliberately shaped like the Tactician's real output: an index plus a reason."""

    action_index: int = Field(description="Index of the chosen action, from the list given.")
    justification: str = Field(description="One short line explaining the choice.")


LEGAL_ACTIONS = [
    "Draw a card from the deck",
    "Play a card from your hand",
    "Pass the turn",
]


def main() -> int:
    with TrajectoryWriter("hello", config=config.describe()) as traj:
        traj.emit(
            "core_event",
            event="legal_actions_enumerated",
            note="Enumerated by code, not by the model - the model only picks an index.",
            actions=LEGAL_ACTIONS,
        )

        def must_be_legal(result) -> None:
            idx = result.parsed.action_index
            if not 0 <= idx < len(LEGAL_ACTIONS):
                raise ValueError(
                    f"action_index {idx} is not one of the "
                    f"{len(LEGAL_ACTIONS)} legal actions (0..{len(LEGAL_ACTIONS) - 1})"
                )

        numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(LEGAL_ACTIONS))
        result = call_llm(
            agent="tactician",
            system=(
                "You play a card game. You will be given a numbered list of legal "
                "actions. Choose exactly one by its index. You may not invent an "
                "action that is not on the list."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Your hand is empty and the deck has 12 cards.\n\n"
                        f"Legal actions:\n{numbered}\n\nChoose one."
                    ),
                }
            ],
            output_format=Choice,
            validate=must_be_legal,
            max_tokens=1024,
            trajectory=traj,
        )

        chosen = LEGAL_ACTIONS[result.parsed.action_index]
        traj.emit(
            "core_event",
            event="action_applied",
            action_index=result.parsed.action_index,
            action=chosen,
        )
        path = traj.path

    cost = "unpriced" if result.cost_usd is None else f"${result.cost_usd:.6f}"
    print(f"model       {result.model}")
    print(f"chose       [{result.parsed.action_index}] {chosen}")
    print(f"because     {result.parsed.justification}")
    print(f"attempts    {result.attempts}")
    print(f"tokens      in={result.input_tokens} out={result.output_tokens}")
    print(f"cost        {cost}")
    print(f"latency     {result.latency_ms} ms")
    print(f"trajectory  {path}")
    print(f"\nRead it with:\n  python -m trajectory.show \"{path}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
