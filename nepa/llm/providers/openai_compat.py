"""Single-attempt OpenAI-compatible provider adapter."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from ...config import ProviderConfig
from ..client import (
    DecodingError,
    LLMConfigurationError,
    LLMRequest,
    LLMResponse,
    ParameterSupportState,
    ProviderError,
    TransportError,
)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text") for part in content if isinstance(part, Mapping) and isinstance(part.get("text"), str)]
        if parts and len(parts) == len(content):
            return "".join(parts)
    raise DecodingError("provider response content is not text")


def _parameter_support(payload: Mapping[str, Any]) -> dict[str, ParameterSupportState]:
    support: dict[str, ParameterSupportState] = {"temperature": ParameterSupportState.UNKNOWN}
    reported = payload.get("parameter_support")
    if not isinstance(reported, Mapping):
        metadata = payload.get("provider_metadata")
        reported = metadata.get("parameter_support") if isinstance(metadata, Mapping) else None
    if isinstance(reported, Mapping):
        value = reported.get("temperature")
        if value in {state.value for state in ParameterSupportState if state is not ParameterSupportState.UNKNOWN}:
            support["temperature"] = ParameterSupportState(value)
    return support


def normalise_chat_response(
    payload: Mapping[str, Any],
    *,
    provider_name: str,
    selected_model: str,
    native_schema: bool,
) -> LLMResponse:
    try:
        choices = payload["choices"]
        first = choices[0]
        message = first["message"]
        text = _content_text(message["content"])
        usage = payload["usage"]
        tokens_in = usage["prompt_tokens"]
        tokens_out = usage["completion_tokens"]
        if not isinstance(tokens_in, int) or not isinstance(tokens_out, int) or tokens_in < 0 or tokens_out < 0:
            raise TypeError("token counts must be non-negative integers")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DecodingError(f"{provider_name} returned a malformed successful response") from exc
    returned_model = payload.get("model", selected_model)
    if not isinstance(returned_model, str) or not returned_model:
        raise DecodingError(f"{provider_name} returned an invalid model identity")
    return LLMResponse(
        text=text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=0,
        model=returned_model,
        cached=False,
        parameter_support=_parameter_support(payload),
        provider_metadata={
            "finish_reason": first.get("finish_reason"),
            "provider": provider_name,
            "native_structured_output": native_schema,
        },
    )


class OpenAICompatibleProvider:
    """Encode and execute one OpenAI chat-completions request."""

    native_structured_output = False

    def __init__(
        self,
        provider_name: str,
        config: ProviderConfig,
        *,
        client: httpx.Client | None = None,
        env_lookup: Callable[[str], str | None] = os.getenv,
    ) -> None:
        if config.kind != "openai_compat":
            raise LLMConfigurationError(f"provider {provider_name} is not openai_compat")
        self.provider_name = provider_name
        self.config = config
        self.client = client or httpx.Client()
        self._env_lookup = env_lookup

    @property
    def endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    def _api_key(self) -> str:
        if not self.config.api_key_env:
            raise LLMConfigurationError(f"provider {self.provider_name} has no configured API key environment name")
        value = self._env_lookup(self.config.api_key_env)
        if not value:
            raise LLMConfigurationError(
                f"missing API key for provider {self.provider_name} in {self.config.api_key_env}"
            )
        return value

    @staticmethod
    def _payload(request: LLMRequest, model: str, native_schema: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if native_schema:
            if request.json_schema is None:
                raise LLMConfigurationError("native structured mode requires a JSON Schema")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "nepa_response", "schema": request.json_schema, "strict": True},
            }
        return payload

    def complete(self, request: LLMRequest, *, model: str, native_schema: bool = False) -> LLMResponse:
        api_key = self._api_key()
        try:
            response = self.client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=self._payload(request, model, native_schema),
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
            raise TransportError(
                f"{self.provider_name} request failed: {exc.__class__.__name__}",
                provider=self.provider_name,
            ) from exc
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise ProviderError(
                f"{self.provider_name} returned HTTP {response.status_code}",
                provider=self.provider_name,
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except (ValueError, httpx.DecodingError) as exc:
            raise DecodingError(f"{self.provider_name} returned a malformed successful response") from exc
        return normalise_chat_response(
            payload,
            provider_name=self.provider_name,
            selected_model=model,
            native_schema=native_schema,
        )
