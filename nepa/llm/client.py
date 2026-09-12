"""Typed provider-neutral LLM contracts and target validation."""

from __future__ import annotations

from enum import Enum
import json
import re
import time
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import ResolvedConfig, configured_model_price
from .telemetry import calculate_cost


class LLMError(RuntimeError):
    """Base class for failures owned by the LLM layer."""


class LLMRequestError(LLMError):
    """The logical request is invalid before provider I/O."""


class LLMConfigurationError(LLMError):
    """The selected provider/model or required configuration is invalid."""


class TransportError(LLMError):
    """The bounded transport policy could not complete a request."""

    def __init__(self, message: str, *, provider: str, retryable: bool = True) -> None:
        self.provider = provider
        self.retryable = retryable
        super().__init__(message)


class ProviderError(LLMError):
    """A provider returned a non-success response or unusable envelope."""

    def __init__(self, message: str, *, provider: str, status_code: int | None = None) -> None:
        self.provider = provider
        self.status_code = status_code
        self.retryable = status_code == 429 or (status_code is not None and status_code >= 500)
        super().__init__(message)


class DecodingError(LLMError):
    """A successful provider response could not be normalized."""


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
    messages: list[dict[str, str]] | None = None
    json_schema: dict[str, Any] | list[Any] | None = None
    temperature: float = Field(ge=0)
    max_tokens: int = Field(gt=0)

    @field_validator("role")
    @classmethod
    def role_is_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("role must not be blank")
        return value


class LLMResponse(_LLMModel):
    text: str
    parsed: Any | None = None
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    model: str = Field(min_length=1)
    cached: bool = False
    parameter_support: dict[str, ParameterSupportState]
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
class Provider(Protocol):
    """One provider-owned, single-attempt wire operation."""

    native_structured_output: bool

    def complete(self, request: LLMRequest, *, model: str, native_schema: bool) -> LLMResponse:
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

    def complete(self, request: LLMRequest, *, store: Any, task_id: str) -> LLMResponse:
        import os
        from .providers.openai_compat import OpenAICompatibleProvider
        coder = self.config.coder
        model = request.model or coder.model
        if model not in {coder.model, coder.fast_model}:
            raise LLMConfigurationError(f"request model is not configured for coding: {model}")
        if coder.provider not in self.providers:
            env_name = self.config.providers[coder.provider].api_key_env
            if not env_name or not os.getenv(env_name):
                raise LLMConfigurationError(f"missing configured API credential: {env_name}")
        provider = self._provider(coder.provider)
        # Both configured adapters use exactly this chat payload, with no duplicate schema.
        wire = OpenAICompatibleProvider._payload(request, model, False)
        wire_bytes = len(json.dumps(wire, ensure_ascii=False).encode())
        if wire_bytes > coder.context_max_bytes:
            raise LLMRequestError(f"actual wire request exceeds {coder.context_max_bytes} bytes: {wire_bytes}")
        price = configured_model_price(self.config, coder.provider, model)
        reservation = calculate_cost(price, wire_bytes + 64, request.max_tokens)
        for attempt in range(3):
            sequence = store.reserve_call(task_id, reservation, wire)
            started = time.monotonic()
            try:
                response = provider.complete(request, model=model, native_schema=False)
            except (TransportError, ProviderError) as exc:
                store.fail_call(sequence, exc, elapsed_s=time.monotonic() - started)
                if exc.retryable and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except BaseException as exc:
                store.fail_call(sequence, exc, elapsed_s=time.monotonic() - started)
                raise
            response.cost_usd = calculate_cost(price, response.tokens_in, response.tokens_out)
            try:
                response.parsed = extract_first_json_value(response.text)
            except ValueError:
                response.parsed = None
            store.settle_call(sequence, response.model_dump(mode="json"), elapsed_s=time.monotonic() - started)
            return response
        raise AssertionError("bounded retry loop fell through")


def extract_first_json_value(text: str) -> Any:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        for match in re.finditer(r"[{\[]", stripped):
            try:
                return decoder.raw_decode(stripped[match.start():])[0]
            except ValueError:
                continue
    raise ValueError("response contains no JSON value")


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
