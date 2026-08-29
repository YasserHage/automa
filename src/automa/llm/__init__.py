"""LLM access. Every model call in Automa goes through :func:`llm.call.call_llm`."""

from automa.llm.call import LLMCallError, LLMResult, call_llm

__all__ = ["LLMCallError", "LLMResult", "call_llm"]
