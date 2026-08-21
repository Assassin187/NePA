"""Single-attempt OpenAI-compatible provider adapter."""

from __future__ import annotations

import json
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


DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=60.0)


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


def _stream_decoding_error(provider_name: str, detail: str) -> DecodingError:
    return DecodingError(f"{provider_name} returned a malformed streaming response: {detail}")


def _stream_usage(payload: Mapping[str, Any], *, provider_name: str) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, Mapping):
        raise _stream_decoding_error(provider_name, "usage is not an object")
    tokens_in = usage.get("prompt_tokens")
    tokens_out = usage.get("completion_tokens")
    if (
        isinstance(tokens_in, bool)
        or not isinstance(tokens_in, int)
        or tokens_in < 0
        or isinstance(tokens_out, bool)
        or not isinstance(tokens_out, int)
        or tokens_out < 0
    ):
        raise _stream_decoding_error(provider_name, "usage token counts are invalid")
    return tokens_in, tokens_out


def _complete_chat_stream(
    client: httpx.Client,
    *,
    endpoint: str,
    headers: Mapping[str, str],
    request: LLMRequest,
    model: str,
    native_schema: bool,
    provider_name: str,
) -> LLMResponse:
    """Complete one OpenAI Chat Completions request through its SSE stream."""

    payload = OpenAICompatibleProvider._payload(request, model, native_schema)
    text_parts: list[str] = []
    returned_model: str | None = None
    finish_reason: str | None = None
    usage: tuple[int, int] | None = None
    saw_done = False
    parameter_support: dict[str, ParameterSupportState] = {"temperature": ParameterSupportState.UNKNOWN}

    try:
        with client.stream("POST", endpoint, headers=dict(headers), json=payload) as response:
            if not 200 <= response.status_code < 300:
                raise ProviderError(
                    f"{provider_name} returned HTTP {response.status_code}",
                    provider=provider_name,
                    status_code=response.status_code,
                )
            for line in response.iter_lines():
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    raise _stream_decoding_error(provider_name, "SSE line is not a data event")
                data = line[5:].lstrip(" ")
                if not data:
                    raise _stream_decoding_error(provider_name, "empty data event")
                if data.strip() == "[DONE]":
                    saw_done = True
                    break
                try:
                    event = json.loads(data)
                except (TypeError, ValueError) as exc:
                    raise _stream_decoding_error(provider_name, "event is not valid JSON") from exc
                if not isinstance(event, Mapping):
                    raise _stream_decoding_error(provider_name, "event is not an object")

                event_model = event.get("model")
                if event_model is not None:
                    if not isinstance(event_model, str) or not event_model:
                        raise _stream_decoding_error(provider_name, "model identity is invalid")
                    if returned_model is not None and event_model != returned_model:
                        raise _stream_decoding_error(provider_name, "model identity changed during stream")
                    returned_model = event_model

                choices = event.get("choices")
                if choices is not None:
                    if not isinstance(choices, list):
                        raise _stream_decoding_error(provider_name, "choices is not an array")
                    if choices:
                        first = choices[0]
                        if not isinstance(first, Mapping):
                            raise _stream_decoding_error(provider_name, "first choice is not an object")
                        delta = first.get("delta", {})
                        if not isinstance(delta, Mapping):
                            raise _stream_decoding_error(provider_name, "delta is not an object")
                        content = delta.get("content")
                        if content is not None:
                            if not isinstance(content, str):
                                raise _stream_decoding_error(provider_name, "delta content is not text")
                            text_parts.append(content)
                        event_finish = first.get("finish_reason")
                        if event_finish is not None:
                            if not isinstance(event_finish, str) or not event_finish:
                                raise _stream_decoding_error(provider_name, "finish_reason is invalid")
                            finish_reason = event_finish
                elif event.get("usage") is None:
                    raise _stream_decoding_error(provider_name, "event has neither choices nor usage")

                event_usage = _stream_usage(event, provider_name=provider_name)
                if event_usage is not None:
                    usage = event_usage
                reported_support = _parameter_support(event)
                for key, value in reported_support.items():
                    if value is not ParameterSupportState.UNKNOWN:
                        parameter_support[key] = value
    except ProviderError:
        raise
    except DecodingError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
        raise TransportError(
            f"{provider_name} streaming request failed: {exc.__class__.__name__}",
            provider=provider_name,
        ) from exc

    if not saw_done:
        raise _stream_decoding_error(provider_name, "stream ended before [DONE]")
    if returned_model is None:
        raise _stream_decoding_error(provider_name, "stream did not return a model identity")
    if usage is None:
        raise _stream_decoding_error(provider_name, "stream did not return final usage")
    if finish_reason is None:
        raise _stream_decoding_error(provider_name, "stream did not return finish_reason")

    return LLMResponse(
        text="".join(text_parts),
        tokens_in=usage[0],
        tokens_out=usage[1],
        cost_usd=0,
        model=returned_model,
        cached=False,
        parameter_support=parameter_support,
        provider_metadata={
            "finish_reason": finish_reason,
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
        self.client = client or httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT)
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
            "stream": True,
            "stream_options": {"include_usage": True},
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
        return _complete_chat_stream(
            self.client,
            endpoint=self.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            request=request,
            model=model,
            native_schema=native_schema,
            provider_name=self.provider_name,
        )
