"""Typed provider-neutral LLM contracts and target validation."""

from __future__ import annotations

from enum import Enum
import json
import re
import time
from typing import Any, Mapping, Protocol

from jsonschema import Draft7Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import ConfigError, ResolvedConfig, configured_model_price
from ..orchestrator import BudgetExhausted, UsageDelta
from ..speclib.lint import canonical_json_bytes
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


class StructuredOutputError(LLMError):
    """A structured response remained invalid after the bounded repair."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        responses: list[LLMResponse] | None = None,
    ) -> None:
        self.errors = errors or []
        self.responses = responses or []
        super().__init__(message)


class EvidenceStorageError(LLMError):
    """Durable cache or trace evidence could not be committed safely."""


class ParameterSupportState(str, Enum):
    REPORTED_APPLIED = "reported_applied"
    REPORTED_IGNORED = "reported_ignored"
    UNKNOWN = "unknown"


class ValidationState(str, Enum):
    PASS = "pass"
    REPAIRED = "repaired"
    FAIL = "fail"


class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMRequest(_LLMModel):
    role: str = Field(min_length=1)
    system: str
    user: str
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
    validation: ValidationState = ValidationState.PASS
    transport_attempts: int = Field(default=1, ge=1)
    repair_attempts: int = Field(default=0, ge=0)


class LLMCallContext(_LLMModel):
    run_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    tier: str = Field(min_length=1)
    task_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    trace_fields: dict[str, Any] = Field(default_factory=dict)


class CapabilityProbeResult(_LLMModel):
    provider: str
    model: str
    parameter: str
    requested_value: Any
    accepted: bool
    returned_model: str | None = None
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None
    state: ParameterSupportState = ParameterSupportState.UNKNOWN
    evidence_kind: str = "request_accepted_only"


class Provider(Protocol):
    """One provider-owned, single-attempt wire operation."""

    native_structured_output: bool

    def complete(self, request: LLMRequest, *, model: str, native_schema: bool) -> LLMResponse:
        """Perform exactly one provider attempt and return normalized data."""


class LLMClient:
    """Own request/target validation before the logical completion path."""

    def __init__(
        self,
        config: ResolvedConfig,
        providers: Mapping[str, Provider] | None = None,
        *,
        orchestrator: Any | None = None,
        store: Any | None = None,
        sleeper: Any = time.sleep,
        backoff: Any = lambda retry_number: 2 ** (retry_number - 1),
        monotonic: Any = time.monotonic,
        telemetry: Any | None = None,
    ) -> None:
        self.config = config
        self.providers = dict(providers or {})
        self.orchestrator = orchestrator
        self.store = store
        self._sleeper = sleeper
        self._backoff = backoff
        self._monotonic = monotonic
        self._pending_evidence: list[LLMResponse] = []
        self._probe_records: list[CapabilityProbeResult] = []
        self.telemetry = telemetry
        if self.telemetry is None and hasattr(store, "read_verified_bytes"):
            from .telemetry import LLMTelemetry

            self.telemetry = LLMTelemetry(
                store,
                secret_env_names={
                    provider.api_key_env
                    for provider in config.providers.values()
                    if provider.api_key_env
                },
            )
        self.cache = None
        if store is not None and hasattr(store, "read_verified_bytes") and hasattr(store, "publish_immutable_json"):
            from .cache import LLMCache

            self.cache = LLMCache(store)

    def validate_request(self, request: LLMRequest) -> LLMRequest:
        if not isinstance(request, LLMRequest):
            raise LLMRequestError("request must be an LLMRequest")
        return request

    def validate_target(self, provider_name: str, model: str) -> None:
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise LLMConfigurationError("provider name must be non-blank")
        if provider_name not in self.config.providers:
            raise LLMConfigurationError(f"provider is not configured: {provider_name}")
        if not isinstance(model, str) or not model.strip():
            raise LLMConfigurationError("model name must be non-blank")
        provider_config = self.config.providers[provider_name]
        if provider_config.kind not in {"openai_compat", "anthropic"}:
            raise LLMConfigurationError(f"unsupported provider kind: {provider_config.kind}")

    def resolve_provider_config(self, provider_name: str, model: str):
        self.validate_target(provider_name, model)
        return self.config.providers[provider_name]

    def _admit_attempt(self) -> None:
        if self.orchestrator is not None and self.store is not None:
            self.orchestrator.admit_external_call(self.store)

    def _transport_complete(self, request: LLMRequest, *, provider_name: str, model: str) -> LLMResponse:
        provider = self.providers.get(provider_name)
        if provider is None:
            raise LLMConfigurationError(f"no adapter registered for provider {provider_name}")
        attempts = 0
        while True:
            attempts += 1
            self._admit_attempt()
            try:
                response = provider.complete(
                    request,
                    model=model,
                    native_schema=bool(request.json_schema is not None and provider.native_structured_output),
                )
                return response.model_copy(
                    update={
                        "transport_attempts": attempts,
                        "provider_metadata": {**response.provider_metadata, "transport_attempts": attempts},
                    }
                )
            except (TransportError, ProviderError) as exc:
                retryable = getattr(exc, "retryable", False)
                if not retryable or attempts >= 4:
                    if isinstance(exc, TransportError):
                        exc.attempts = attempts
                    else:
                        exc.attempts = attempts
                    raise
                self._sleeper(self._backoff(attempts))

    def _charge_response(self, response: LLMResponse, *, provider_name: str, model: str, price: Any) -> LLMResponse:
        cost_usd = calculate_cost(price, response.tokens_in, response.tokens_out)
        charged = response.model_copy(update={"cost_usd": cost_usd})
        if self.orchestrator is not None and self.store is not None:
            try:
                self.orchestrator.record_external_usage(
                    self.store,
                    UsageDelta(tokens_in=response.tokens_in, tokens_out=response.tokens_out, cost_usd=cost_usd),
                )
            except BudgetExhausted as exc:
                evidence = charged.model_copy(update={"validation": ValidationState.FAIL})
                self._pending_evidence.append(evidence)
                exc.completed_response = evidence
                exc.pending_evidence = [evidence]
                raise
        return charged

    @property
    def pending_evidence(self) -> tuple[LLMResponse, ...]:
        return tuple(self._pending_evidence)

    def take_pending_evidence(self) -> list[LLMResponse]:
        evidence = list(self._pending_evidence)
        self._pending_evidence.clear()
        return evidence

    @property
    def probe_records(self) -> tuple[CapabilityProbeResult, ...]:
        return tuple(self._probe_records)

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        try:
            Draft7Validator.check_schema(schema)
        except SchemaError as exc:
            raise LLMRequestError(f"invalid JSON Schema: {exc.message}") from exc

    @staticmethod
    def _validate_template_provenance(context: LLMCallContext | None) -> None:
        if context is None or "prompt_template_sha256" not in context.trace_fields:
            return
        value = context.trace_fields["prompt_template_sha256"]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise LLMRequestError("prompt_template_sha256 must be lowercase 64-character hexadecimal text")

    @staticmethod
    def _fallback_request(request: LLMRequest) -> LLMRequest:
        if request.json_schema is None:
            return request
        schema_text = canonical_json_bytes(request.json_schema).decode("utf-8")
        instruction = (
            "\n\nReturn one JSON value that conforms to this JSON Schema. "
            "Do not omit required fields. JSON Schema:\n" + schema_text
        )
        return request.model_copy(update={"user": request.user + instruction})

    @staticmethod
    def _structured_response(response: LLMResponse, request: LLMRequest, *, native: bool) -> LLMResponse:
        if request.json_schema is None:
            return response
        if response.parsed is not None:
            candidate = response.parsed
        elif native:
            try:
                candidate = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise StructuredOutputError("native structured output is not valid JSON", errors=[
                    {"path": "$", "code": "JSON_DECODE_ERROR", "message": str(exc)},
                ]) from exc
        else:
            candidate = extract_first_json_value(response.text)
        errors = structured_validation_errors(request.json_schema, candidate)
        if errors:
            raise StructuredOutputError("structured output failed Schema validation", errors=errors)
        return response.model_copy(update={"parsed": candidate, "validation": ValidationState.PASS})

    def complete(
        self,
        request: LLMRequest,
        *,
        provider_name: str,
        model: str,
        context: LLMCallContext | None = None,
        use_cache: bool = True,
        _capability_probe: tuple[str, Any] | None = None,
    ) -> LLMResponse:
        def probe_fields(
            response: LLMResponse | None = None,
            *,
            accepted: bool,
            error: BaseException | None = None,
        ) -> dict[str, Any] | None:
            if _capability_probe is None:
                return None
            parameter, requested_value = _capability_probe
            state = (
                response.parameter_support.get(parameter, ParameterSupportState.UNKNOWN)
                if response is not None
                else ParameterSupportState.UNKNOWN
            )
            return {
                "provider": provider_name,
                "model": model,
                "parameter": parameter,
                "requested_value": requested_value,
                "accepted": accepted,
                "returned_model": response.model if response is not None else None,
                "tokens_in": response.tokens_in if response is not None else 0,
                "tokens_out": response.tokens_out if response is not None else 0,
                "cost_usd": response.cost_usd if response is not None else 0,
                "latency_ms": max(0, round((self._monotonic() - started) * 1000)),
                "error": str(error) if error is not None else None,
                "state": state.value if hasattr(state, "value") else state,
                "evidence_kind": "provider_report" if state is not ParameterSupportState.UNKNOWN else ("request_accepted_only" if accepted else "none"),
            }

        started = self._monotonic()
        self.validate_request(request)
        self.validate_target(provider_name, model)
        if request.json_schema is not None:
            self._validate_schema(request.json_schema)
        self._validate_template_provenance(context)
        try:
            price = configured_model_price(self.config, provider_name, model)
        except ConfigError as exc:
            raise LLMConfigurationError(str(exc)) from exc
        provider = self.providers.get(provider_name)
        if provider is None:
            raise LLMConfigurationError(f"no adapter registered for provider {provider_name}")
        key = None
        if use_cache and self.cache is not None:
            from .cache import cache_key

            key = cache_key(
                provider_name=provider_name,
                provider_kind=self.config.providers[provider_name].kind,
                model=model,
                request=request,
            )
            cached = self.cache.load(key)
            if cached is not None:
                cached_response = cached.model_copy(update={"cached": True, "cost_usd": 0.0})
                if self.telemetry is not None:
                    self.telemetry.publish(
                        provider_name=provider_name,
                        request=request,
                        response=cached_response,
                        context=context,
                        provider_requests=[request],
                        provider_responses=[cached_response],
                        cached=True,
                        latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                    )
                return cached_response
        native = bool(request.json_schema is not None and provider.native_structured_output)
        provider_request = request if native else self._fallback_request(request)
        try:
            response = self._transport_complete(provider_request, provider_name=provider_name, model=model)
        except LLMError as exc:
            if self.telemetry is not None:
                self.telemetry.publish_failure(
                    provider_name=provider_name,
                    request=request,
                    provider_requests=[provider_request],
                    error=exc,
                    context=context,
                    latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                    attempts=getattr(exc, "attempts", 1),
                    capability_probe=probe_fields(accepted=False, error=exc),
                )
            raise
        try:
            charged = self._charge_response(response, provider_name=provider_name, model=model, price=price)
        except BudgetExhausted as exc:
            if self.telemetry is not None:
                evidence = getattr(exc, "completed_response", response)
                self.telemetry.publish(
                    provider_name=provider_name,
                    request=request,
                    response=evidence,
                    context=context,
                    provider_requests=[provider_request],
                    provider_responses=[evidence],
                    validation="fail",
                    latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                    capability_probe=probe_fields(evidence, accepted=True, error=exc),
                )
            raise
        if request.json_schema is None:
            final = charged
        else:
            try:
                final = self._structured_response(charged, request, native=native)
            except StructuredOutputError as first_error:
                repair_request = self._repair_request(request, charged, first_error.errors)
                repair_provider_request = repair_request if native else self._fallback_request(repair_request)
                try:
                    repair_raw = self._transport_complete(repair_provider_request, provider_name=provider_name, model=model)
                except LLMError as exc:
                    if self.telemetry is not None:
                        self.telemetry.publish_failure(
                            provider_name=provider_name,
                            request=request,
                            provider_requests=[provider_request, repair_provider_request],
                            responses=[charged],
                            error=exc,
                            context=context,
                            latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                            attempts=charged.transport_attempts + getattr(exc, "attempts", 1),
                            repair_attempts=1,
                            capability_probe=probe_fields(charged, accepted=True, error=exc),
                        )
                    raise
                try:
                    repair_charged = self._charge_response(
                        repair_raw,
                        provider_name=provider_name,
                        model=model,
                        price=price,
                    )
                except Exception as exc:
                    if not hasattr(exc, "completed_response"):
                        setattr(exc, "completed_response", repair_raw)
                    setattr(exc, "prior_responses", [charged])
                    if isinstance(exc, BudgetExhausted) and self.telemetry is not None:
                        evidence = getattr(exc, "completed_response", repair_raw)
                        self.telemetry.publish(
                            provider_name=provider_name,
                            request=request,
                            response=evidence,
                            context=context,
                            provider_requests=[provider_request, repair_provider_request],
                            provider_responses=[charged, evidence],
                            validation="fail",
                            latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                            capability_probe=probe_fields(evidence, accepted=True, error=exc),
                        )
                    raise
                try:
                    repaired = self._structured_response(repair_charged, request, native=native)
                except StructuredOutputError as final_error:
                    final_error.responses = [charged, repair_charged]
                    if self.telemetry is not None:
                        self.telemetry.publish(
                            provider_name=provider_name,
                            request=request,
                            response=repair_charged,
                            context=context,
                            provider_requests=[provider_request, repair_provider_request],
                            provider_responses=[charged, repair_charged],
                            validation="fail",
                            latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                            capability_probe=probe_fields(repair_charged, accepted=True, error=final_error),
                        )
                    raise
                final = repaired.model_copy(
                    update={
                        "tokens_in": charged.tokens_in + repair_charged.tokens_in,
                        "tokens_out": charged.tokens_out + repair_charged.tokens_out,
                        "cost_usd": charged.cost_usd + repair_charged.cost_usd,
                        "model": repair_charged.model,
                        "validation": ValidationState.REPAIRED,
                        "transport_attempts": charged.transport_attempts + repair_charged.transport_attempts,
                        "repair_attempts": 1,
                        "provider_metadata": {
                            **charged.provider_metadata,
                            "repair": repair_charged.provider_metadata,
                        },
                    }
                )
        if key is not None and self.cache is not None:
            self.cache.publish(
                key,
                provider_name=provider_name,
                provider_kind=self.config.providers[provider_name].kind,
                model=model,
                request=request,
                response=final,
            )
        if self.telemetry is not None:
            provider_requests = [provider_request]
            provider_responses = [charged]
            if final.repair_attempts:
                provider_requests.append(repair_provider_request)
                provider_responses.append(repair_charged)
            self.telemetry.publish(
                provider_name=provider_name,
                request=request,
                response=final,
                context=context,
                provider_requests=provider_requests,
                provider_responses=provider_responses,
                latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                capability_probe=probe_fields(final, accepted=True),
            )
        return final

    def probe_parameter(
        self,
        *,
        provider_name: str,
        model: str,
        parameter: str = "temperature",
        requested_value: Any = 0.0,
        context: LLMCallContext | None = None,
    ) -> CapabilityProbeResult:
        if parameter != "temperature":
            raise LLMRequestError(f"unsupported capability probe parameter: {parameter}")
        started = self._monotonic()
        request = LLMRequest(
            role="capability_probe",
            system="Respond with a short unstructured acknowledgement.",
            user="Reply with OK.",
            temperature=requested_value,
            max_tokens=16,
        )
        try:
            response = self.complete(
                request,
                provider_name=provider_name,
                model=model,
                context=context,
                use_cache=False,
                _capability_probe=(parameter, requested_value),
            )
            state = response.parameter_support.get(parameter, ParameterSupportState.UNKNOWN)
            evidence_kind = "provider_report" if state is not ParameterSupportState.UNKNOWN else "request_accepted_only"
            result = CapabilityProbeResult(
                provider=provider_name,
                model=model,
                parameter=parameter,
                requested_value=requested_value,
                accepted=True,
                returned_model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_usd=response.cost_usd,
                latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                state=state,
                evidence_kind=evidence_kind,
            )
        except LLMError as exc:
            result = CapabilityProbeResult(
                provider=provider_name,
                model=model,
                parameter=parameter,
                requested_value=requested_value,
                accepted=False,
                latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                error=str(exc),
                state=ParameterSupportState.UNKNOWN,
                evidence_kind="none",
            )
        self._probe_records.append(result)
        return result

    @staticmethod
    def _repair_request(request: LLMRequest, invalid_response: LLMResponse, errors: list[dict[str, Any]]) -> LLMRequest:
        schema_text = canonical_json_bytes(request.json_schema).decode("utf-8") if request.json_schema is not None else "null"
        errors_text = canonical_json_bytes(errors).decode("utf-8")
        repair_text = (
            "\n\nRepair the previous response. Return only one JSON value matching this JSON Schema."
            "\nJSON Schema:\n" + schema_text
            + "\nPrevious invalid output:\n" + invalid_response.text
            + "\nValidation errors:\n" + errors_text
        )
        return request.model_copy(update={"user": request.user + repair_text})


def extract_first_json_value(text: str) -> Any:
    """Extract the first complete JSON value without greedy brace slicing."""

    decoder = json.JSONDecoder()
    starts = set('{["-0123456789tfn')
    for index, character in enumerate(text):
        if character not in starts:
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    raise StructuredOutputError("structured output contains no complete JSON value", errors=[
        {"path": "$", "code": "JSON_DECODE_ERROR", "message": "no complete JSON value found"},
    ])


def structured_validation_errors(schema: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    validator = Draft7Validator(schema)
    errors = []
    for error in validator.iter_errors(value):
        path = "$"
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        errors.append({"path": path, "code": str(error.validator or "validation"), "message": error.message})
    return sorted(errors, key=lambda item: (item["path"], item["code"], item["message"]))


__all__ = [
    "CapabilityProbeResult",
    "DecodingError",
    "EvidenceStorageError",
    "LLMCallContext",
    "LLMClient",
    "LLMConfigurationError",
    "LLMError",
    "LLMRequest",
    "LLMRequestError",
    "LLMResponse",
    "ParameterSupportState",
    "Provider",
    "ProviderError",
    "StructuredOutputError",
    "TransportError",
    "ValidationState",
]
