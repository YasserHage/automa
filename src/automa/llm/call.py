"""The single LLM call wrapper.

Invariant 3: *every prompt sent and every response received are logged in
trajectories/*. That invariant holds because this is the only function in the
project that talks to a model. Agents, the compiler, and the eval harness all
call through here; nothing else constructs a provider request.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from automa import config
from automa.llm.pricing import cost_usd, pricing_note
from automa.llm.providers import LLMRequest, resolve, split_model
from automa.trajectory.writer import TrajectoryWriter


class LLMCallError(RuntimeError):
    """Raised when a call could not be completed within ``max_attempts``."""


@dataclass(frozen=True)
class LLMResult:
    """Provider-neutral result. No vendor SDK object is reachable from here."""

    text: str
    parsed: BaseModel | None
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    latency_ms: float
    attempts: int
    request_id: str | None


def call_llm(
    *,
    agent: str,
    messages: list[dict[str, Any]],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    output_format: type[BaseModel] | None = None,
    effort: str | None = None,
    thinking: dict[str, Any] | None = None,
    validate: Callable[[LLMResult], None] | None = None,
    max_attempts: int = 3,
    trajectory: TrajectoryWriter | None = None,
    tags: dict[str, Any] | None = None,
) -> LLMResult:
    """Call a model, logging every attempt to the trajectory.

    ``validate`` is the interesting parameter. The SDK already retries transport
    failures invisibly; those are not what the trajectory deliverable asks to see.
    This retries *semantic* failures - a Tactician returning an action index that
    is not in the legal list, a Compiler emitting a module that fails validation -
    and feeds the rejection reason back into the next attempt, so the retry is
    informed rather than a re-roll. Raise from ``validate`` to reject a response.

    Args:
        agent: Role name; selects the model via :func:`config.model_for` and labels
            every logged event.
        validate: Called with the candidate result. Raising rejects it.
        max_attempts: Total attempts, including the first.

    Raises:
        LLMCallError: Every attempt was rejected or failed.
    """
    model_id = model or config.model_for(agent)
    provider_name, bare_model = split_model(model_id)
    provider = resolve(provider_name)
    config.require_credentials(provider_name)

    attempt_messages = list(messages)
    last_rejection: str | None = None

    for attempt in range(1, max_attempts + 1):
        request = LLMRequest(
            model=bare_model,
            messages=attempt_messages,
            system=system,
            max_tokens=max_tokens,
            output_format=output_format,
            effort=effort,
            thinking=thinking,
        )

        started = time.monotonic()
        try:
            response = provider.complete(request)
        except Exception as exc:
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            _emit(
                trajectory,
                agent=agent,
                model=model_id,
                attempt=attempt,
                outcome="api_error",
                system=system,
                messages=attempt_messages,
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
                tags=tags,
            )
            if trajectory is not None:
                trajectory.record_failed_attempt()
            if not provider.is_retryable(exc):
                # A bad model name, a missing entitlement, a rejected key. Retrying
                # cannot help and degrading to a fallback would hide a
                # misconfiguration behind ninety turns of random play, so this one
                # propagates unchanged and loudly.
                raise
            last_rejection = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                continue
            # Out of attempts on a *transient* failure. Honour the documented
            # contract - callers are told to expect LLMCallError - so that a
            # provider outage reaches the one handler written for it. Raising the
            # raw vendor exception here instead is what let a run of 503s kill a
            # match whose Tactician had a working fallback ready.
            raise LLMCallError(
                f"{agent}: no response from {model_id} after {max_attempts} attempts. "
                f"Last failure: {last_rejection}"
            ) from exc

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        cost = cost_usd(
            model_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
        )

        result = LLMResult(
            text=response.text,
            parsed=response.parsed,
            model=model_id,
            stop_reason=response.stop_reason,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            attempts=attempt,
            request_id=response.request_id,
        )

        rejection: str | None = None
        if validate is not None:
            try:
                validate(result)
            except Exception as exc:
                rejection = f"{type(exc).__name__}: {exc}"

        _emit(
            trajectory,
            agent=agent,
            model=model_id,
            attempt=attempt,
            outcome="ok" if rejection is None else "validation_failed",
            system=system,
            messages=attempt_messages,
            response_text=response.text,
            response_content=response.raw_content,
            parsed=result.parsed.model_dump() if result.parsed is not None else None,
            stop_reason=response.stop_reason,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            cost_usd=cost,
            pricing_note=pricing_note(model_id),
            latency_ms=latency_ms,
            request_id=response.request_id,
            rejection=rejection,
            tags=tags,
        )
        if trajectory is not None:
            trajectory.record_llm_usage(
                cost_usd=cost,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )

        if rejection is None:
            return result

        last_rejection = rejection
        # Show the model what it produced and why it was rejected, so the next
        # attempt is corrective rather than a fresh roll of the dice.
        attempt_messages = [
            *attempt_messages,
            {"role": "assistant", "content": response.text},
            {
                "role": "user",
                "content": (
                    f"That response was rejected: {rejection}\n"
                    "Correct it and reply again in the required format."
                ),
            },
        ]

    raise LLMCallError(
        f"{agent}: no valid response from {model_id} after {max_attempts} attempts. "
        f"Last rejection: {last_rejection}"
    )


def _loggable(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages with image bytes replaced by a descriptor.

    Invariant 3 asks for the full prompt on the record. Taken literally for an image
    that means a few hundred kilobytes of base64 per call, which would bury the text
    the log exists to make readable. A digest identifies the image exactly - the same
    file always produces the same entry, and a different one is immediately visible -
    without the payload.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            out.append(message)
            continue
        parts: list[Any] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image":
                data = part.get("data") or b""
                parts.append(
                    {
                        "type": "image",
                        "mime_type": part.get("mime_type"),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()[:16],
                    }
                )
            else:
                parts.append(part)
        out.append({**message, "content": parts})
    return out


def _emit(trajectory: TrajectoryWriter | None, **fields: Any) -> None:
    if trajectory is not None:
        if "messages" in fields:
            fields["messages"] = _loggable(fields["messages"])
        trajectory.emit("llm_call", **fields)
