"""Typed provider-neutral LLM contracts and target validation."""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, replace
import hashlib
import json
import time
from typing import Any, Literal, Mapping, Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import ModelCapabilities, ResolvedConfig, configured_model_price
from .telemetry import calculate_cost, price_usage


class LLMError(RuntimeError):
    """Base class for failures owned by the LLM layer."""
    retryable = False
    failure_class = "llm_error"
    observed_raw_response: dict[str, Any] | None = None


class LLMRequestError(LLMError):
    """The logical request is invalid before provider I/O."""
    failure_class = "request"


class LLMConfigurationError(LLMError):
    """The selected provider/model or required configuration is invalid."""
    failure_class = "configuration"


class TransportError(LLMError):
    """The bounded transport policy could not complete a request."""
    failure_class = "transport"

    def __init__(self, message: str, *, provider: str, retryable: bool = True) -> None:
        self.provider = provider
        self.retryable = retryable
        super().__init__(message)


class ProviderError(LLMError):
    """A provider returned a non-success response or unusable envelope."""

    def __init__(self, message: str, *, provider: str, status_code: int | None = None) -> None:
        self.provider = provider
        self.status_code = status_code
        self.retryable = status_code == 429 or (status_code is not None and 500 <= status_code < 600)
        self.failure_class = "rate_limit" if status_code == 429 else ("server" if self.retryable else "http_request")
        super().__init__(message)


class DecodingError(LLMError):
    """A successful provider response could not be normalized."""
    failure_class = "malformed_stream"


class ResponseIdentityError(DecodingError):
    failure_class = "identity"


class ResponseUsageError(DecodingError):
    failure_class = "usage"


class ParameterSupportState(str, Enum):
    REPORTED_APPLIED = "reported_applied"
    REPORTED_IGNORED = "reported_ignored"
    UNKNOWN = "unknown"


class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMRequest(_LLMModel):
    role: str = Field(min_length=1)
    system: str
    user: str
    model: str | None = None
    action_format: Literal["json_object", "tool_calls"] = "json_object"
    messages: list[dict[str, Any]] | None = None
    json_schema: dict[str, Any] | list[Any] | None = None
    temperature: float = Field(ge=0)
    max_tokens: int = Field(gt=0)
    stop: list[str] | None = None

    @field_validator("role")
    @classmethod
    def role_is_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("role must not be blank")
        return value


class LLMResponse(_LLMModel):
    text: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_content: str = ""
    parsed: Any | None = None
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_cny: float = Field(ge=0)
    model: str = Field(min_length=1)
    pricing: dict[str, Any] | None = None
    cached: bool = False
    parameter_support: dict[str, ParameterSupportState]
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.text or None,
                                   "reasoning_content": self.reasoning_content}
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        return message


def action_tools(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": entry["properties"]["tool"]["const"],
              "parameters": entry["properties"]["arguments"]}} for entry in schema["oneOf"]]


def decode_action(response: LLMResponse, action_format: str, schema: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """Only a complete outer JSON action or one genuine native function may execute."""
    if response.provider_metadata.get("finish_reason") in {"length", "content_filter"}:
        return None, [{"path": [], "message": "Incomplete provider response; no tool executed."}]
    if action_format == "tool_calls":
        if len(response.tool_calls) != 1:
            return None, [{"path": [], "message": "Exactly one native tool call is required; no tool executed."}]
        call = response.tool_calls[0]
        if not call.get("id") or call.get("type") != "function" or not isinstance(call.get("function"), dict):
            return None, [{"path": [], "message": "Invalid native tool envelope; no tool executed."}]
        function = call["function"]
        raw = function.get("arguments", "")
    else:
        raw = response.text
    try:
        action = json.loads(raw)
        if action_format == "tool_calls":
            action = {"tool": function.get("name"), "arguments": action}
        return action, structured_validation_errors(action, schema)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, [{"path": [], "message": f"Invalid JSON: {exc}. Return the complete action/arguments with all closing braces."}]


@dataclass(frozen=True)
class PreparedRequest:
    """Provider-owned HTTP bytes shared by context sizing, reservation and sending."""
    provider_name: str
    model: str
    wire: dict[str, Any]
    body: bytes
    input_reserve_tokens: int
    billable_output_tokens: int
    capabilities: ModelCapabilities
    reservation_cny: float = 0
    request_sha256: str = ""

    @property
    def wire_bytes(self) -> int:
        return len(self.body)


class Provider(Protocol):
    """One provider-owned, single-attempt wire operation."""

    native_structured_output: bool

    def prepare(self, request: LLMRequest, *, model: str, capabilities: ModelCapabilities,
                native_schema: bool = False) -> PreparedRequest:
        """Prepare bytes without credentials or network I/O."""

    def send(self, prepared: PreparedRequest) -> LLMResponse:
        """Perform exactly one provider attempt and return normalized data."""



class LLMClient:
    """Single-attempt adapters plus bounded retry, durable accounting and evidence."""
    def __init__(self, config: ResolvedConfig, providers: Mapping[str, Provider] | None = None) -> None:
        self.config = config
        self.providers = dict(providers or {})

    def _provider(self, name: str) -> Provider:
        if name not in self.providers:
            from .providers import AnthropicProvider, OpenAICompatibleProvider
            config = self.config.providers[name]
            if config.kind == "anthropic":
                self.providers[name] = AnthropicProvider(name, config)
            elif config.kind == "openai_compat":
                self.providers[name] = OpenAICompatibleProvider(name, config)
            else:
                raise LLMConfigurationError(f"unsupported provider kind: {config.kind}")
        return self.providers[name]

    def prepare(self, request: LLMRequest) -> PreparedRequest:
        coder = self.config.coder
        model = request.model or coder.model
        if model not in {coder.model, coder.fast_model}:
            raise LLMConfigurationError(f"request model is not configured for coding: {model}")
        provider = self._provider(coder.provider)
        capabilities = self.config.capabilities[f"{coder.provider}/{model}"]
        prepared = provider.prepare(request, model=model, capabilities=capabilities)
        price = configured_model_price(self.config, coder.provider, model)
        reservation = calculate_cost(price, prepared.input_reserve_tokens, prepared.billable_output_tokens)
        return replace(prepared, reservation_cny=reservation,
                       request_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest())

    def wire_payload(self, request: LLMRequest) -> dict[str, Any]:
        return self.prepare(request).wire

    def complete(self, request: LLMRequest, *, store: Any, task_id: str,
                 prepared: PreparedRequest | None = None) -> LLMResponse:
        if prepared is None:
            prepared = self.prepare(request)
        elif (prepared.request_sha256 != hashlib.sha256(request.model_dump_json().encode()).hexdigest()
              or prepared.model != (request.model or self.config.coder.model)
              or prepared.provider_name != self.config.coder.provider):
            raise LLMRequestError("prepared request does not match request/selected model")
        if prepared.wire_bytes > self.config.coder.context_max_bytes:
            raise LLMRequestError(f"actual wire request exceeds {self.config.coder.context_max_bytes} bytes: {prepared.wire_bytes}")
        provider = self._provider(prepared.provider_name)
        price = configured_model_price(self.config, prepared.provider_name, prepared.model)
        for attempt in range(3):
            sequence = store.reserve_call(task_id, prepared.reservation_cny, prepared.wire,
                                          phase=self.config.campaign.phase)
            started = time.monotonic()
            response = None
            try:
                response = provider.send(prepared)
                # Injected providers implement the same observed envelope contract.
                metadata = response.provider_metadata
                if (metadata.get("returned_model_identity_observed") is not True
                    or metadata.get("returned_model_identity") != prepared.model
                    or response.model != prepared.model):
                    raise ResponseIdentityError("missing or mismatched observed model identity")
                usage = metadata.get("usage")
                if not isinstance(usage, Mapping):
                    raise ResponseUsageError("missing observed provider usage")
                from .providers.openai_compat import normalize_usage
                metadata.update(normalize_usage(usage, prepared.capabilities.usage))
                if response.tokens_in != usage["prompt_tokens"] or response.tokens_out != usage["completion_tokens"]:
                    raise ResponseUsageError("normalized token counts do not match observed usage")
                try:
                    response.pricing = price_usage(price, response.tokens_in, response.tokens_out,
                        started_at=store.run["pending_calls"][str(sequence)]["started_at"],
                        cache_hit_tokens=metadata["cache_hit_tokens"])
                except ValueError as exc:
                    raise ResponseUsageError(str(exc)) from exc
            except (TransportError, ProviderError) as exc:
                store.fail_call(sequence, exc, elapsed_s=time.monotonic() - started,
                                raw_response=getattr(exc, "observed_raw_response", None))
                if exc.retryable and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except BaseException as exc:
                if isinstance(exc, LLMError) and response is not None:
                    exc.observed_raw_response = response.provider_metadata.get("observed_raw_response", response.model_dump(mode="json"))
                store.fail_call(sequence, exc, elapsed_s=time.monotonic() - started,
                                raw_response=getattr(exc, "observed_raw_response", None))
                raise
            response.cost_cny = response.pricing["cost_cny"]
            response.provider_metadata["requested_model_identity"] = prepared.model
            try:
                response.parsed = json.loads(response.text)
            except ValueError:
                response.parsed = None
            store.settle_call(sequence, response.model_dump(mode="json"), elapsed_s=time.monotonic() - started)
            return response
        raise AssertionError("bounded retry loop fell through")


def structured_validation_errors(value: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    # A known action has one relevant branch. Report its actual missing/invalid
    # arguments instead of dumping the whole response in a generic oneOf error.
    if isinstance(value, dict):
        for branch in schema.get("oneOf", []):
            if branch.get("properties", {}).get("tool", {}).get("const") == value.get("tool"):
                schema = branch
                break
    return [{"path": list(error.absolute_path), "message": error.message}
            for error in Draft202012Validator(schema).iter_errors(value)]
