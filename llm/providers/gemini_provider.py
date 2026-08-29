"""Google Gemini backend - NOT IMPLEMENTED.

Placeholder for running the per-turn Tactician on Gemini Flash's free tier, which
would take cost per turn to zero for the highest-frequency agent in the system.

To implement, the following must be filled in from Google's own SDK documentation
rather than guessed:

1. **Client and auth** - the `google-genai` package and its API-key resolution,
   added to ``pyproject.toml`` and ``.env.example``.
2. **Structured output** - Gemini's ``response_schema`` / ``response_mime_type``
   equivalent of ``output_format``, returning something that validates against the
   caller's Pydantic model. ``LLMResponse.parsed`` must stay a Pydantic instance so
   callers cannot tell the providers apart.
3. **Usage fields** - Gemini reports token counts under different names
   (prompt/candidates/cached). Map them onto ``LLMResponse``'s fields, and do not
   silently report zero for a count the API does not return.
4. **Pricing entry** - register ``google:<model>`` in :mod:`llm.pricing` with
   ``free_tier=True`` so its $0 is labelled rather than ambiguous.
5. **Retry policy** - free tiers rate-limit aggressively; ``is_retryable`` should
   treat quota errors as retryable and the trajectory should show the wait.

Two things to settle before switching the Tactician over:

* Free-tier terms of service. Hackathon ground rule 3 requires every tool to be
  used within its license and service terms, and ground rule 7 restricts what data
  may be sent. Confirm game material may be sent to a free-tier endpoint.
* Fairness disclosure. Running the Tactician on a free tier while the baseline runs
  a paid model is a meaningful difference in resources between the two arms of the
  comparison, and must be stated explicitly in the README and changelog.
"""

from __future__ import annotations

from llm.providers.base import LLMRequest, LLMResponse


class GeminiProvider:
    name = "google"

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError(
            "The Gemini provider is a stub. See the module docstring in "
            "llm/providers/gemini_provider.py for what it needs before use."
        )

    def is_retryable(self, exc: BaseException) -> bool:
        return False
