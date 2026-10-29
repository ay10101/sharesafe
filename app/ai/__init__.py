"""
ShareSafe AI package.
Contains LLM completion wrapper and intelligence utilities.
"""
from app.ai.llm import LLMResult, complete, clear_cache

__all__ = ["LLMResult", "complete", "clear_cache"]
