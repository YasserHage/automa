"""A scripted backend, shared by every test that exercises the model seam.

Deliberately not a mocked SDK: these tests drive a hand-written provider through the
same :class:`~automa.llm.providers.base.Provider` interface a real backend uses. If
that seam were fake, the tests could not be written this way - so they double as proof
that a second provider drops in without touching `call_llm` or any of its callers.

It is also what keeps the whole suite offline. Nothing here opens a socket, and
`conftest.py` installs placeholder credentials so a genuine call would fail loudly
rather than quietly succeed.

Not named ``test_*``, so pytest collects nothing from this file.
"""

from __future__ import annotations

from pydantic import BaseModel

from automa.llm.providers.base import LLMRequest, LLMResponse


class Decision(BaseModel):
    """Structurally what a Tactician returns, for tests that need no more than that."""

    action_index: int
    justification: str


class ScriptedProvider:
    """Returns a canned response per attempt and records what it was sent.

    An entry that is an exception is raised instead of returned, which is how the
    retry, transport-failure and give-up paths are driven.
    """

    name = "scripted"

    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self._responses[len(self.requests) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, TimeoutError)


def structured_response(
    parsed: BaseModel,
    *,
    input_tokens: int = 120,
    output_tokens: int = 20,
    request_id: str | None = None,
) -> LLMResponse:
    """Wrap any parsed model as a provider response."""
    return LLMResponse(
        text=parsed.model_dump_json(),
        parsed=parsed,
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_id=request_id or "req_test",
        raw_content=[{"type": "text", "text": parsed.model_dump_json()}],
    )


def decision_response(index: int, justification: str | None = None) -> LLMResponse:
    """A response choosing ``index``, whether or not that index is legal."""
    return structured_response(
        Decision(action_index=index, justification=justification or f"picked {index}"),
        request_id=f"req_{index}",
    )
