"""The deterministic core.

Everything in this package is pure Python with no model in the loop. It owns match
state, enumerates legal actions from the game module, and applies transitions.

Two invariants are enforced here structurally rather than by convention:

1. No LLM decides legality. The core enumerates; the LLM chooses an index.
2. No LLM writes state. Only the core applies transitions.

Which is why **no module under core/ may import `anthropic`, `llm`, or any provider
SDK**. ``tests/test_core_is_llm_free.py`` fails the build if one ever does. That
test is the executable form of the project's central architectural claim; if it
starts failing, the claim is no longer true.

Populated in Phase 1 (game module contract) and Phase 2 (match loop).
"""
