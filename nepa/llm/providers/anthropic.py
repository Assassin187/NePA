"""Single-attempt Anthropic gateway adapter with exact configured routing."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from ...config import ProviderConfig
from ..client import LLMConfigurationError, LLMRequest, LLMResponse
from .openai_compat import DEFAULT_HTTP_TIMEOUT, OpenAICompatibleProvider, _complete_chat_stream


class AnthropicProvider:
    """Use the configured Anthropic gateway URL byte-for-byte as the request target."""

    native_structured_output = False

    def __init__(
        self,
        provider_name: str,
        config: ProviderConfig,
        *,
        client: httpx.Client | None = None,
        env_lookup: Callable[[str], str | None] = os.getenv,
    ) -> None:
        if config.kind != "anthropic":
            raise LLMConfigurationError(f"provider {provider_name} is not anthropic")
        self.provider_name = provider_name
        self.config = config
        self.client = client or httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT)
        self._env_lookup = env_lookup

    @property
    def endpoint(self) -> str:
        return self.config.base_url

    def _api_key(self) -> str:
        if not self.config.api_key_env:
            raise LLMConfigurationError(f"provider {self.provider_name} has no configured API key environment name")
        value = self._env_lookup(self.config.api_key_env)
        if not value:
            raise LLMConfigurationError(
                f"missing API key for provider {self.provider_name} in {self.config.api_key_env}"
            )
        return value

    def complete(self, request: LLMRequest, *, model: str, native_schema: bool = False) -> LLMResponse:
        api_key = self._api_key()
        return _complete_chat_stream(
            self.client,
            endpoint=self.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            request=request,
            model=model,
            native_schema=native_schema,
            provider_name=self.provider_name,
        )
