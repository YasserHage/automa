"""Shared test setup.

The whole suite runs offline and must pass on a machine that has never held an API
key - that is what makes `pytest` a safe first command in the reproduction guide.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def placeholder_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the credential preflight without a real key.

    Nothing here reaches a network - providers are replaced with fakes - but
    `call_llm` checks for a key before dispatching, so the check needs something to
    find. Placeholders keep that path exercised rather than bypassed.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
