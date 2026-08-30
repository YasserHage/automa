"""Health and configuration - what the UI reads before it offers anything."""

from __future__ import annotations

import os

from fastapi import APIRouter

from automa import config
from automa.web.models import ConfigResponse, HealthResponse

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True)


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    """Models, the trajectory directory, and credential *presence* only.

    Never a key or any part of one - the typed response model is what stops a future
    field leaking one by accident. It drives an honest empty state: with no
    `GEMINI_API_KEY`, the UI disables live play and compiling up front rather than
    failing minutes into a compile.
    """
    return ConfigResponse(
        models={role: config.model_for(role) for role in config.DEFAULT_MODELS},
        trajectory_dir=str(config.TRAJECTORY_DIR),
        credentials={
            provider: bool(os.environ.get(var))
            for provider, var in config.PROVIDER_CREDENTIALS.items()
        },
    )
