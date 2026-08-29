"""Automa - an AI agent that plays physical board and card games against you.

Two zones, kept structurally apart:

* :mod:`automa.core` is deterministic. It holds match state, enumerates legal
  actions from the game module, and applies transitions. No model is ever in that
  loop, which is why rule hallucination is impossible by construction rather than
  by instruction.
* :mod:`automa.llm` is the only place a model is reached, through the single
  wrapper :func:`automa.llm.call_llm`.

See ``plan.md`` for the phase plan and ``CLAUDE.md`` for the invariants.
"""

__version__ = "0.1.0"
