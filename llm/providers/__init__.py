"""Provider registry.

Models are named ``provider:model``; the prefix selects the backend.
"""

from __future__ import annotations

import functools

from llm.providers.base import LLMRequest, LLMResponse, Provider

__all__ = ["LLMRequest", "LLMResponse", "Provider", "resolve", "split_model"]


def split_model(identifier: str) -> tuple[str, str]:
    """Split ``"anthropic:claude-opus-5"`` into ``("anthropic", "claude-opus-5")``."""
    provider, sep, model = identifier.partition(":")
    if not sep or not provider or not model:
        raise ValueError(
            f"Model {identifier!r} must be written 'provider:model', "
            f"e.g. 'anthropic:claude-opus-5'."
        )
    return provider, model


@functools.cache
def resolve(provider_name: str) -> Provider:
    """Return the backend for a provider name.

    Imports are deferred so that a missing optional SDK only breaks the provider
    that needs it, not the whole program.
    """
    if provider_name == "anthropic":
        from llm.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if provider_name == "google":
        from llm.providers.gemini_provider import GeminiProvider

        return GeminiProvider()
    raise ValueError(f"Unknown provider {provider_name!r}. Known providers: anthropic, google.")
