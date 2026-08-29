"""Anthropic backend."""

from __future__ import annotations

import functools
from typing import Any

import anthropic

from llm.providers.base import LLMRequest, LLMResponse


@functools.lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    """One shared client. Credentials resolve from ANTHROPIC_API_KEY (loaded from
    .env by :mod:`config`) or from an `ant auth login` profile."""
    return anthropic.Anthropic()


class AnthropicProvider:
    name = "anthropic"

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": request.messages,
        }
        if request.system is not None:
            kwargs["system"] = request.system
        if request.thinking is not None:
            kwargs["thinking"] = request.thinking
        if request.effort is not None:
            kwargs["output_config"] = {"effort": request.effort}

        client = _client()
        if request.output_format is not None:
            message = client.messages.parse(output_format=request.output_format, **kwargs)
            parsed = message.parsed_output
        else:
            message = client.messages.create(**kwargs)
            parsed = None

        text = "".join(b.text for b in message.content if b.type == "text")
        usage = message.usage
        return LLMResponse(
            text=text,
            parsed=parsed,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            request_id=message._request_id,
            raw_content=[b.model_dump() for b in message.content],
        )

    def is_retryable(self, exc: BaseException) -> bool:
        # The SDK already retries 429/5xx internally. These are the failures that
        # survive that and might still succeed on a fresh attempt.
        if isinstance(exc, anthropic.APIConnectionError | anthropic.RateLimitError):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code >= 500
        return False
