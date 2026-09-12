"""Provider-neutral LLM contracts."""
from .client import LLMClient, LLMRequest, LLMResponse
__all__ = ["LLMClient", "LLMRequest", "LLMResponse"]
