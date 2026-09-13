"""Single-attempt OpenAI-compatible provider adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx

from ...config import ModelCapabilities, ProviderConfig
from ..client import (
    DecodingError,
    LLMConfigurationError,
    LLMRequest,
    LLMResponse,
    LLMError,
    LLMRequestError,
    PreparedRequest,
    ResponseIdentityError,
    ResponseUsageError,
    ParameterSupportState,
    ProviderError,
    TransportError,
    action_tools,
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
        if isinstance(value, str) and value in {state.value for state in ParameterSupportState if state is not ParameterSupportState.UNKNOWN}:
            support["temperature"] = ParameterSupportState(value)
    return support


def _stream_decoding_error(provider_name: str, detail: str) -> DecodingError:
    return DecodingError(f"{provider_name} returned a malformed streaming response: {detail}")


def _stream_usage(payload: Mapping[str, Any], *, provider_name: str) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, Mapping):
        raise ResponseUsageError("usage is not an object")
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
        raise ResponseUsageError("usage token counts are invalid")
    return tokens_in, tokens_out


def normalize_usage(usage: Mapping[str, Any], kind: str) -> dict[str, Any]:
    counts = _stream_usage({"usage": usage}, provider_name=kind)
    if counts is None:
        raise ResponseUsageError("missing usage")
    tokens_in, tokens_out = counts
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != tokens_in + tokens_out):
        raise ResponseUsageError("total usage does not sum to input and output")
    details = usage.get("prompt_tokens_details")
    output_details = usage.get("completion_tokens_details")
    if details is not None and not isinstance(details, Mapping):
        raise ResponseUsageError("invalid prompt token details")
    if output_details is not None and not isinstance(output_details, Mapping):
        raise ResponseUsageError("invalid completion token details")
    hit = usage.get("prompt_cache_hit_tokens") if kind == "deepseek" else (details or {}).get("cached_tokens")
    miss = usage.get("prompt_cache_miss_tokens") if kind == "deepseek" else None
    reasoning = (output_details or {}).get("reasoning_tokens")
    for count, limit in ((hit, tokens_in), (miss, tokens_in), (reasoning, tokens_out)):
        if count is not None and (type(count) is not int or not 0 <= count <= limit):
            raise ResponseUsageError("invalid cache/reasoning usage")
    if hit is not None and miss is not None and hit + miss != tokens_in:
        raise ResponseUsageError("cache usage does not sum to input tokens")
    return {"cache_hit_tokens": hit, "cache_miss_tokens": tokens_in - (hit or 0),
            "cache_usage_basis": "provider" if hit is not None else "missing_assumed_all_miss",
            "reasoning_tokens": reasoning, "reasoning_usage_basis": "provider" if reasoning is not None else "unknown"}


def _sse_events(raw: str, provider_name: str) -> Iterator[str]:
    data: list[str] = []
    # SSE line endings are CR/LF; Unicode separators inside JSON strings are data.
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines[:-1]:
        if not line:
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field not in {"event", "id", "retry"}:
            raise _stream_decoding_error(provider_name, "invalid SSE field")
    if data or lines[-1]:
        raise _stream_decoding_error(provider_name, "incomplete SSE event")


def _complete_chat_stream(client: httpx.Client, *, endpoint: str, headers: Mapping[str, str],
                          prepared: PreparedRequest) -> LLMResponse:
    """Send exactly the measured bytes; retain all observed text even on failure."""
    raw_parts: list[str] = []
    status_code = None
    try:
        with client.stream("POST", endpoint, headers=dict(headers), content=prepared.body) as response:
            status_code = response.status_code
            for part in response.iter_text():
                raw_parts.append(part)
            if not 200 <= status_code < 300:
                raise ProviderError(f"{prepared.provider_name} returned HTTP {status_code}",
                                    provider=prepared.provider_name, status_code=status_code)
        return _parse_chat_stream("".join(raw_parts), prepared)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
        error = TransportError(f"{prepared.provider_name} streaming request failed: {exc.__class__.__name__}",
                               provider=prepared.provider_name)
        error.observed_raw_response = {"status_code": status_code, "sse": "".join(raw_parts)}
        raise error from exc
    except LLMError as exc:
        exc.observed_raw_response = {"status_code": status_code, "sse": "".join(raw_parts)}
        raise
    except BaseException as exc:
        # Deadline alarms/interruption still retain the bytes already observed.
        setattr(exc, "observed_raw_response", {"status_code": status_code, "sse": "".join(raw_parts)})
        raise


def _parse_chat_stream(raw: str, prepared: PreparedRequest) -> LLMResponse:
    provider_name = prepared.provider_name
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_parts: dict[int, dict[str, Any]] = {}
    returned_model: str | None = None
    finish_reason: str | None = None
    usage: tuple[int, int] | None = None
    usage_details: dict[str, Any] = {}
    saw_done = False
    parameter_support: dict[str, ParameterSupportState] = {"temperature": ParameterSupportState.UNKNOWN}

    for data in _sse_events(raw, provider_name):
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
                raise ResponseIdentityError("model identity is invalid")
            if returned_model is not None and event_model != returned_model:
                raise ResponseIdentityError("model identity changed during stream")
            returned_model = event_model

        choices = event.get("choices")
        if choices is not None:
            if not isinstance(choices, list):
                raise _stream_decoding_error(provider_name, "choices is not an array")
            if choices:
                if len(choices) != 1:
                    raise _stream_decoding_error(provider_name, "expected one choice")
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
                reasoning = delta.get("reasoning_content")
                if reasoning is not None:
                    if not isinstance(reasoning, str):
                        raise _stream_decoding_error(provider_name, "reasoning_content is not text")
                    reasoning_parts.append(reasoning)
                tool_deltas = delta.get("tool_calls")
                if tool_deltas is not None and not isinstance(tool_deltas, list):
                    raise _stream_decoding_error(provider_name, "tool_calls is not an array")
                for tool in tool_deltas or []:
                    if not isinstance(tool, Mapping) or type(tool.get("index")) is not int or tool["index"] < 0:
                        raise _stream_decoding_error(provider_name, "invalid tool call index")
                    part = tool_parts.setdefault(tool["index"], {"function": {"name": "", "arguments": ""}})
                    for key in ("id", "type"):
                        if tool.get(key) is not None:
                            if not isinstance(tool[key], str) or (key in part and part[key] != tool[key]):
                                raise _stream_decoding_error(provider_name, f"invalid/changing tool {key}")
                            part[key] = tool[key]
                    function = tool.get("function", {})
                    if not isinstance(function, Mapping):
                        raise _stream_decoding_error(provider_name, "invalid tool function")
                    for key in ("name", "arguments"):
                        if function.get(key) is not None:
                            if not isinstance(function[key], str):
                                raise _stream_decoding_error(provider_name, f"tool {key} is not text")
                            part["function"][key] += function[key]
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
            usage_details = dict(event["usage"])
            normalized = normalize_usage(usage_details, prepared.capabilities.usage)
        reported_support = _parameter_support(event)
        for key, value in reported_support.items():
            if value is not ParameterSupportState.UNKNOWN:
                parameter_support[key] = value
    if not saw_done:
        raise _stream_decoding_error(provider_name, "stream ended before [DONE]")
    if usage is None:
        raise ResponseUsageError("stream did not return final usage")
    if finish_reason is None:
        raise _stream_decoding_error(provider_name, "stream did not return finish_reason")

    if returned_model is None:
        raise ResponseIdentityError("stream did not return model identity")
    if returned_model != prepared.model:
        raise ResponseIdentityError("returned model identity does not match requested model")
    return LLMResponse(
        text="".join(text_parts),
        tool_calls=[tool_parts[index] for index in sorted(tool_parts)],
        reasoning_content="".join(reasoning_parts),
        tokens_in=usage[0],
        tokens_out=usage[1],
        cost_cny=0,
        model=returned_model,
        cached=False,
        parameter_support=parameter_support,
        provider_metadata={
            "finish_reason": finish_reason,
            "usage": usage_details,
            "provider": provider_name,
            "native_structured_output": prepared.wire.get("response_format", {}).get("type") == "json_schema",
            "requested_model_identity": prepared.model,
            "observed_raw_response": {"sse": raw},
            **normalized,
            "returned_model_identity": returned_model,
            "returned_model_identity_observed": returned_model is not None,
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
                *(request.messages if request.messages is not None else [{"role": "user", "content": request.user}]),
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.action_format == "tool_calls":
            if not isinstance(request.json_schema, dict):
                raise LLMConfigurationError("tool_calls requires the action schema")
            payload["tools"] = action_tools(request.json_schema)
            payload["tool_choice"] = "auto"
        elif native_schema:
            if request.json_schema is None:
                raise LLMConfigurationError("native structured mode requires a JSON Schema")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "nepa_response", "schema": request.json_schema, "strict": True},
            }
        elif request.action_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def prepare(self, request: LLMRequest, *, model: str, capabilities: ModelCapabilities,
                native_schema: bool = False) -> PreparedRequest:
        if not capabilities.stream:
            raise LLMConfigurationError("streaming usage is required")
        if native_schema and (not capabilities.json_schema or request.action_format == "tool_calls"):
            raise LLMConfigurationError("unsupported native schema combination")
        if request.action_format == "tool_calls" and not capabilities.tool_calls:
            raise LLMConfigurationError("model does not support native tools")
        if request.action_format == "json_object" and not capabilities.json_object:
            raise LLMConfigurationError("model does not support JSON object output")
        if request.max_tokens > capabilities.max_output_tokens:
            raise LLMRequestError("requested output exceeds configured model capability")
        if request.temperature < capabilities.temperature_min or (
            capabilities.temperature_max_exclusive is not None and request.temperature >= capabilities.temperature_max_exclusive
        ):
            raise LLMRequestError("temperature is outside the configured model range")
        if request.stop is not None and not capabilities.stop:
            raise LLMConfigurationError("model profile does not allow stop")
        payload = self._payload(request, model, native_schema)
        if request.action_format == "json_object" and capabilities.json_requires_instruction:
            if "json" not in json.dumps(payload["messages"], ensure_ascii=False).lower():
                raise LLMRequestError("JSON object mode requires a JSON instruction")
        controls = capabilities.wire
        payload[controls.output_token_field] = payload.pop("max_tokens")
        for key in ("enable_thinking", "preserve_thinking"):
            value = getattr(controls, key)
            if value is not None:
                payload[key] = value
        if request.action_format == "tool_calls" and controls.parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = controls.parallel_tool_calls
        if request.stop is not None:
            payload["stop"] = request.stop
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        # One token per UTF-8 byte plus a fixed chat framing allowance. Include
        # every message/tool definition and per-message framing in the bound.
        input_bound = len(body) + 64 + 16 * len(payload["messages"])
        output_bound = request.max_tokens + controls.completion_reserve_tokens
        input_limit = (capabilities.thinking_max_input_tokens if controls.enable_thinking else capabilities.max_input_tokens)
        if input_limit is not None and input_bound > input_limit:
            raise LLMRequestError("input reservation exceeds configured model input capability")
        if capabilities.context_tokens is not None and input_bound + output_bound > capabilities.context_tokens:
            raise LLMRequestError("input/output reservation exceeds configured model context capability")
        return PreparedRequest(self.provider_name, model, json.loads(body), body, input_bound, output_bound, capabilities)

    def send(self, prepared: PreparedRequest) -> LLMResponse:
        api_key = self._api_key()
        return _complete_chat_stream(
            self.client, endpoint=self.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            prepared=prepared,
        )

    def complete(self, request: LLMRequest, *, model: str, capabilities: ModelCapabilities,
                 native_schema: bool = False) -> LLMResponse:
        return self.send(self.prepare(request, model=model, capabilities=capabilities, native_schema=native_schema))
