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

# The model every role uses unless told otherwise. Kept as a single constant rather
# than repeated per role, because the one property that must not drift is that the
# baseline and the agents run on the *same* model - see below.
DEFAULT_MODEL = "google:gemini-3.5-flash-lite"

#: Sets the default for every role at once. Per-role `AUTOMA_MODEL_<ROLE>` still wins,
#: so a sweep can move one agent without disturbing the others.
GLOBAL_MODEL_VAR = "AUTOMA_MODEL"

# Per-role models. Every role runs on the same Gemini Flash Lite model for now. Two
# consequences worth knowing:
#
#   * Cost per turn is ~$0, so the free tier's rate limits, not cost, are the
#     binding constraint on how fast the evaluation battery can run.
#   * Baseline and solution share one model, which makes the Phase 3 comparison
#     fair by construction - there is no "the agent got a better model" objection
#     to answer, and no resource difference to disclose.
#
# The Anthropic backend stays fully implemented, so switching a role is a change here,
# or an AUTOMA_MODEL / AUTOMA_MODEL_<ROLE> override with no code change at all.
DEFAULT_MODELS: dict[str, str] = {
    "strategist": DEFAULT_MODEL,
    "compiler": DEFAULT_MODEL,
    "tactician": DEFAULT_MODEL,
    # The baseline must be a *fair* comparison, not a strawman. Same model as the
    # solution's agents; the difference under test is the architecture, not the
    # model. See plan.md Phase 3.
    "baseline": DEFAULT_MODEL,
}


# Credentials are per provider, never shared. A match may legitimately run two
# providers at once - Strategist on one, Tactician on another - so a single generic
# key could not express the configuration this project supports. Each SDK also
# resolves its own variable by convention, so these names are not ours to choose.
PROVIDER_CREDENTIALS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
}


class MissingCredentialError(RuntimeError):
    """A provider was selected but its API key is not set."""


def require_credentials(provider: str) -> None:
    """Fail early and legibly when a provider's key is absent.

    Without this the failure surfaces from inside the vendor SDK at call time,
    which is a poor first experience for anyone running the project from a clean
    environment - and being runnable from a clean environment is a graded
    requirement, not a nicety.
    """
    var = PROVIDER_CREDENTIALS.get(provider)
    if var is None:
        return  # unknown provider; resolve() reports that with a better message
    if not os.environ.get(var):
        raise MissingCredentialError(
            f"{var} is not set, but a '{provider}' model was requested. "
            f"Add it to {REPO_ROOT / '.env'} (see .env.example) or set it in your "
            f"environment. Keys are per provider - see PROVIDER_CREDENTIALS."
        )


def model_for(role: str) -> str:
    """Return the ``provider:model`` string for an agent role.

    Resolution order, most specific first:

    1. ``AUTOMA_MODEL_<ROLE>`` - one role, for sweeping a single agent;
    2. ``AUTOMA_MODEL`` - every role at once, for moving the whole system to a
       different model without editing code;
    3. :data:`DEFAULT_MODELS`.

    The unknown-role check happens before any override is read, so a typo in a role
    name is reported as a typo rather than silently picking up the global default.
    """
    if role not in DEFAULT_MODELS:
        raise ValueError(
            f"No model configured for role {role!r}. "
            f"Known roles: {sorted(DEFAULT_MODELS)}. "
            f"Set AUTOMA_MODEL_{role.upper()} to override."
        )
    return (
        os.environ.get(f"AUTOMA_MODEL_{role.upper()}")
        or os.environ.get(GLOBAL_MODEL_VAR)
        or DEFAULT_MODELS[role]
    )


def describe() -> dict[str, object]:
    """Config snapshot recorded in every trajectory's ``run_start`` event."""
    return {
        "models": {role: model_for(role) for role in DEFAULT_MODELS},
        "trajectory_dir": str(TRAJECTORY_DIR),
    }
