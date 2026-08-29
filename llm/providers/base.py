"""The provider seam.

Everything above this file - agents, the compiler, the eval harness - sees only
:class:`LLMRequest` and :class:`LLMResponse`. No vendor SDK object crosses this
boundary, which is what lets the per-turn Tactician move to a different provider
without touching a caller, the trajectory schema, or the cost accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class LLMRequest:
    """One provider-neutral model call."""

    model: str
    """Bare model name, provider prefix already stripped."""

    messages: list[dict[str, Any]]
    system: str | None = None
    max_tokens: int = 4096
    output_format: type[BaseModel] | None = None
    """When set, the provider must return JSON validating against this schema."""
    effort: str | None = None
    thinking: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    """One provider-neutral model response."""

    text: str
    parsed: BaseModel | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    request_id: str | None = None
    raw_content: list[dict[str, Any]] = field(default_factory=list)
    """Full response content, every block, logged verbatim to satisfy invariant 3."""


class Provider(Protocol):
    """What a backend must implement to be usable by :func:`llm.call.call_llm`."""

    name: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        ...

    def is_retryable(self, exc: BaseException) -> bool:
        """Whether a transport-level failure is worth another attempt."""
        ...
