"""Trajectory event schema.

A trajectory is a JSONL file: one JSON object per line, one file per run. The
format is a hackathon deliverable in its own right (representative trajectories
for every agent), so it is defined here once, before any agent exists, and every
later phase writes into it.

Bump :data:`SCHEMA_VERSION` whenever an event's shape changes incompatibly, so a
trajectory recorded by an older version is still interpretable.
"""

from __future__ import annotations

from typing import Final, Literal

SCHEMA_VERSION: Final = 1

EventType = Literal[
    "run_start",
    "llm_call",
    "core_event",
    "human_checkpoint",
    "error",
    "note",
    "run_end",
]

#: Outcome of a single LLM attempt.
#: ``validation_failed`` is the interesting one - it is the feedback that shaped
#: the next attempt, which is exactly what the trajectory deliverable asks to see.
CallOutcome = Literal["ok", "validation_failed", "api_error"]
