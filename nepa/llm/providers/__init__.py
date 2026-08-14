"""Built-in single-attempt provider adapters."""

from .anthropic import AnthropicProvider
from .openai_compat import OpenAICompatibleProvider

__all__ = ["AnthropicProvider", "OpenAICompatibleProvider"]
