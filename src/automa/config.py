"""Runtime configuration for Automa.

Model identifiers are always written ``provider:model``. The prefix selects the
backend in :mod:`llm.providers`, so swapping the Tactician onto a different
provider is a config change rather than a code change.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# src/automa/config.py -> automa -> src -> repo root. Correct for the editable
# install the README documents; set AUTOMA_TRAJECTORY_DIR to override.
REPO_ROOT = Path(__file__).resolve().parents[2]
TRAJECTORY_DIR = Path(os.environ.get("AUTOMA_TRAJECTORY_DIR", REPO_ROOT / "trajectories"))

# Per-role models. The Strategist and Compiler reason hard but run rarely, so they
# get the strongest model. The Tactician runs on every single turn and cost per turn
# is a reported metric, so it starts on the cheapest model that can pick an index
# from an enumerated list.
DEFAULT_MODELS: dict[str, str] = {
    "strategist": "anthropic:claude-opus-5",
    "compiler": "anthropic:claude-opus-5",
    "tactician": "anthropic:claude-haiku-4-5",
    # The baseline must be a *fair* comparison, not a strawman, so it gets a capable
    # model even though that makes the solution's win harder to show. See plan.md
    # Phase 3.
    "baseline": "anthropic:claude-opus-5",
}


def model_for(role: str) -> str:
    """Return the ``provider:model`` string for an agent role.

    ``AUTOMA_MODEL_<ROLE>`` overrides the default, so an evaluation run can sweep
    models without editing code.
    """
    override = os.environ.get(f"AUTOMA_MODEL_{role.upper()}")
    if override:
        return override
    try:
        return DEFAULT_MODELS[role]
    except KeyError:
        raise ValueError(
            f"No model configured for role {role!r}. "
            f"Known roles: {sorted(DEFAULT_MODELS)}. "
            f"Set AUTOMA_MODEL_{role.upper()} to override."
        ) from None


def describe() -> dict[str, object]:
    """Config snapshot recorded in every trajectory's ``run_start`` event."""
    return {
        "models": {role: model_for(role) for role in DEFAULT_MODELS},
        "trajectory_dir": str(TRAJECTORY_DIR),
    }
