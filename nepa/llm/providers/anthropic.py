"""Single-attempt Anthropic gateway adapter with exact configured routing."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from ...config import ProviderConfig
from ..client import DecodingError, LLMConfigurationError, LLMRequest, LLMResponse, ProviderError, TransportError
from .openai_compat import OpenAICompatibleProvider, normalise_chat_response


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
        self.client = client or httpx.Client()
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
        payload = OpenAICompatibleProvider._payload(request, model, native_schema)
        try:
            response = self.client.post(
                self.endpoint,
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
            raise TransportError(
                f"{self.provider_name} request failed: {exc.__class__.__name__}",
                provider=self.provider_name,
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.provider_name} returned HTTP {response.status_code}",
                provider=self.provider_name,
                status_code=response.status_code,
            )
        try:
            response_payload = response.json()
        except (ValueError, httpx.DecodingError) as exc:
            raise DecodingError(f"{self.provider_name} returned a malformed successful response") from exc
        return normalise_chat_response(
            response_payload,
            provider_name=self.provider_name,
            selected_model=model,
            native_schema=native_schema,
        )
