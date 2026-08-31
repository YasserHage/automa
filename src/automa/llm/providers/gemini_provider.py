"""Google Gemini backend.

Written against the installed ``google-genai`` SDK surface rather than from
memory: config fields, usage-metadata names and error classes were all read off
the package before this was implemented.

Structured output uses Gemini's native ``response_schema``, which accepts a
Pydantic model directly and returns a validated instance on ``response.parsed`` -
so ``instructor`` is not needed here after all. Retry and trajectory logging stay
with :func:`automa.llm.call_llm`; nothing in this file retries on its own, because
an attempt that does not pass through ``call_llm`` never reaches the trajectory and
invariant 3 would no longer hold.

**Free-tier note.** Submitting content to the free tier is subject to Google's
terms, which differ from paid usage. Automa only ever sends game state and rule
material, and invariant 6 already forbids committing commercial rulebook text, so
keep generated and hand-written game material original, public-domain or synthetic.
"""

from __future__ import annotations

import functools
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from automa.llm.providers.base import LLMRequest, LLMResponse

#: Effort levels mapped onto Gemini's thinking budget, so callers can express
#: "think harder" without knowing which provider is behind the seam.
_EFFORT_BUDGETS = {"low": 0, "medium": 2048, "high": 8192, "xhigh": 16384, "max": 24576}

#: `HttpOptions.timeout` is unset by default, so a congested or wedged server
#: hangs the call indefinitely - the failure mode this bounds. Milliseconds, per
#: the SDK's own convention. Retryable (see is_retryable), so a call that hits
#: this ceiling loses 5 minutes to a fresh attempt rather than the whole compile
#: losing however long the server was actually going to take.
_REQUEST_TIMEOUT_MS = 5 * 60 * 1000


@functools.lru_cache(maxsize=1)
def _client() -> genai.Client:
    """One shared client. The SDK resolves GEMINI_API_KEY from the environment,
    which :mod:`automa.config` has already loaded from .env and checked."""
    return genai.Client()


def _to_parts(content: Any) -> list[types.Part]:
    """One message's content as Gemini Parts.

    A plain string is the ordinary case. The list form carries images - a scanned
    rulebook page, a card photograph - which is how rule material reaches a model
    without bypassing ``call_llm``.
    """
    if isinstance(content, str):
        return [types.Part(text=content)]
    if not isinstance(content, list):
        return [types.Part(text=str(content))]

    parts: list[types.Part] = []
    for part in content:
        if not isinstance(part, dict):
            parts.append(types.Part(text=str(part)))
        elif part.get("type") == "image":
            parts.append(
                types.Part.from_bytes(
                    data=part["data"], mime_type=part.get("mime_type", "image/jpeg")
                )
            )
        else:
            parts.append(types.Part(text=part.get("text", "")))
    return parts


def _to_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    """Translate the neutral message list into Gemini's Content list.

    Gemini names the assistant role "model"; the retry loop in ``call_llm`` appends
    an assistant turn followed by a user turn when a response is rejected, so this
    mapping has to be right for corrective retries to work at all.
    """
    return [
        types.Content(
            role="model" if m.get("role") == "assistant" else "user",
            parts=_to_parts(m.get("content", "")),
        )
        for m in messages
    ]


def _thinking_config(
    thinking: dict[str, Any] | None, effort: str | None
) -> types.ThinkingConfig | None:
    if thinking is not None:
        native = dict(thinking)
        # Accept the neutral {"budget": N} spelling, and drop Anthropic-shaped keys
        # so the same call site can target either provider.
        if "budget" in native:
            native["thinking_budget"] = native.pop("budget")
        native.pop("type", None)
        native.pop("display", None)
        return types.ThinkingConfig(**native) if native else None
    if effort is not None and effort in _EFFORT_BUDGETS:
        return types.ThinkingConfig(thinking_budget=_EFFORT_BUDGETS[effort])
    return None


class GeminiProvider:
    name = "google"

    def complete(self, request: LLMRequest) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            max_output_tokens=request.max_tokens,
            thinking_config=_thinking_config(request.thinking, request.effort),
            # Automatic function calling is on by default and would let the SDK make
            # extra model calls on its own. Those calls would bypass call_llm and so
            # never reach the trajectory, breaking invariant 3. Automa passes no
            # tools, so switching AFC off costs nothing and removes the SDK's
            # "direct use of AFC is not recommended" warning as a side effect.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
        )
        if request.output_format is not None:
            config.response_mime_type = "application/json"
            config.response_schema = request.output_format

        response = _client().models.generate_content(
            model=request.model,
            contents=_to_contents(request.messages),
            config=config,
        )

        candidate = (response.candidates or [None])[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        stop_reason = getattr(finish_reason, "name", None) or (
            str(finish_reason) if finish_reason else None
        )

        text = response.text or ""
        if not text and stop_reason == "MAX_TOKENS":
            # Thinking tokens are drawn from the same budget as the answer, so a
            # tight max_tokens can be spent entirely on reasoning and return
            # nothing. Say so plainly instead of handing back an empty string.
            raise RuntimeError(
                f"Gemini returned no text: the {request.max_tokens}-token output budget "
                "was exhausted, most likely by thinking. Raise max_tokens, or pass "
                'thinking={"budget": 0} to switch thinking off for this call.'
            )

        usage = response.usage_metadata
        # Thinking tokens are billed as output, so fold them in rather than
        # under-reporting what the turn actually cost.
        output_tokens = (getattr(usage, "candidates_token_count", 0) or 0) + (
            getattr(usage, "thoughts_token_count", 0) or 0
        )

        raw_content: list[dict[str, Any]] = []
        for cand in response.candidates or []:
            if cand.content and cand.content.parts:
                raw_content.extend(
                    part.model_dump(exclude_none=True) for part in cand.content.parts
                )

        return LLMResponse(
            text=text,
            parsed=response.parsed if request.output_format is not None else None,
            stop_reason=stop_reason,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=output_tokens,
            cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
            cache_write_tokens=0,  # Gemini reports no separate cache-write count
            request_id=response.response_id,
            raw_content=raw_content,
        )

    def is_retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, errors.ServerError):
            return True
        if isinstance(exc, errors.ClientError):
            # 429 matters here: the free tier rate-limits aggressively, and the
            # retry is logged so the trajectory shows the wait rather than hiding it.
            return getattr(exc, "code", None) == 429
        if isinstance(exc, httpx.TimeoutException | httpx.ConnectError):
            # What _REQUEST_TIMEOUT_MS turns a hung request into. A wedged
            # connection is exactly the kind of transient failure a fresh
            # attempt might not hit again.
            return True
        return False
