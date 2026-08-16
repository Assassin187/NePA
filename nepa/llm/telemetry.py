"""LLM usage pricing primitives; durable telemetry is added in task 6."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from ..config import ModelPrice
from ..run_store import ArtifactRef, RunStore, RunStoreError


def calculate_cost(price: ModelPrice, tokens_in: int, tokens_out: int) -> float:
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("token counts cannot be negative")
    return (
        tokens_in / 1_000_000 * price.input_usd_per_million_tokens
        + tokens_out / 1_000_000 * price.output_usd_per_million_tokens
    )


class LLMTelemetry:
    """Publish complete LLM evidence before one canonical logical trace row."""

    _OPTIONAL_FIELDS = {
        "compiler_phase",
        "work_package_id",
        "parent_artifact_sha256",
        "finish_reason",
        "local_repair_budget_hit",
        "global_repair_budget_hit",
        "prompt_template_sha256",
    }

    def __init__(
        self,
        store: RunStore,
        *,
        utcnow: Callable[[], datetime] | None = None,
        fault_hook: Callable[[str], None] | None = None,
        secret_values: set[str] | None = None,
        secret_env_names: set[str] | None = None,
    ) -> None:
        self.store = store
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._fault_hook = fault_hook
        self._secret_values = {value for value in (secret_values or set()) if value}
        self._secret_env_names = {value for value in (secret_env_names or set()) if value}

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _timestamp(self) -> str:
        current = self._utcnow()
        return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _redact(self, value: Any) -> Any:
        secrets = self._secret_values | {
            os.getenv(name, "") for name in self._secret_env_names
        }
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    @staticmethod
    def _prompt_text(request: Any) -> str:
        return request.system + "\n" + request.user

    def _publish_prompt(self, sequence: int, suffix: str, request: Any) -> ArtifactRef:
        self._fault("llm_prompt_before_publish")
        data = self._redact(self._prompt_text(request)).encode("utf-8")
        ref = self.store.publish_immutable_bytes(f"trace/prompts/{sequence:06d}{suffix}.txt", data)
        self.store.verify_ref(ref)
        self._fault("llm_prompt_published")
        return ref

    def _publish_output(self, sequence: int, suffix: str, response: Any) -> ArtifactRef:
        value = {
            "text": response.text,
            "parsed": response.parsed,
            "model": response.model,
            "provider_metadata": response.provider_metadata,
        }
        self._fault("llm_output_before_publish")
        data = json.dumps(self._redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ref = self.store.publish_immutable_bytes(f"trace/outputs/{sequence:06d}{suffix}.json", data)
        self.store.verify_ref(ref)
        self._fault("llm_output_published")
        return ref

    def _publish_failure_output(self, sequence: int, error: BaseException, suffix: str = "") -> ArtifactRef:
        value = {"error": self._redact(str(error))}
        self._fault("llm_output_before_publish")
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ref = self.store.publish_immutable_bytes(f"trace/outputs/{sequence:06d}{suffix}.json", data)
        self.store.verify_ref(ref)
        self._fault("llm_output_published")
        return ref

    def publish(
        self,
        *,
        provider_name: str,
        request: Any,
        response: Any,
        context: Any | None = None,
        provider_requests: list[Any] | None = None,
        provider_responses: list[Any] | None = None,
        validation: str | None = None,
        cached: bool | None = None,
        latency_ms: int = 0,
        transport_attempts: int | None = None,
        repair_attempts: int | None = None,
        optional_fields: Mapping[str, Any] | None = None,
        capability_probe: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        requests = provider_requests or [request]
        responses = provider_responses or [response]
        sequence = self.store.next_llm_call_sequence()
        prompt_refs = [self._publish_prompt(sequence, "" if index == 0 else f".repair{index}", item) for index, item in enumerate(requests)]
        output_refs = [self._publish_output(sequence, "" if index == 0 else f".repair{index}", item) for index, item in enumerate(responses)]
        self._fault("llm_evidence_published")
        primary_prompt = self.store.read_verified_bytes(prompt_refs[0].path, prompt_refs[0].sha256)
        tokens_in = sum(item.tokens_in for item in responses)
        tokens_out = sum(item.tokens_out for item in responses)
        cost_usd = sum(item.cost_usd for item in responses)
        model = f"{provider_name}/{response.model}"
        trace: dict[str, Any] = {
            "ts": self._timestamp(),
            "run_id": getattr(context, "run_id", self.store.run_id),
            "stage": getattr(context, "stage", "llm"),
            "agent_role": request.role,
            "tier": getattr(context, "tier", "unknown"),
            "task_id": getattr(context, "task_id", None),
            "attempt": getattr(context, "attempt", 1),
            "model": model,
            "params_requested": {"temperature": request.temperature, "max_tokens": request.max_tokens},
            "parameter_support": {key: value.value if hasattr(value, "value") else value for key, value in response.parameter_support.items()},
            "prompt_sha256": hashlib.sha256(primary_prompt).hexdigest(),
            "prompt_path": prompt_refs[0].path,
            "output_path": output_refs[-1].path,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "validation": validation or (response.validation.value if hasattr(response.validation, "value") else response.validation),
            "cached": response.cached if cached is None else cached,
            "transport_attempts": transport_attempts if transport_attempts is not None else response.transport_attempts,
            "repair_attempts": repair_attempts if repair_attempts is not None else response.repair_attempts,
            "provider_metadata": self._redact(response.provider_metadata),
            "provider_prompt_paths": [ref.path for ref in prompt_refs],
            "provider_output_paths": [ref.path for ref in output_refs],
        }
        if context is not None:
            trace.update({key: value for key, value in context.trace_fields.items() if key in self._OPTIONAL_FIELDS})
        if optional_fields:
            trace.update({key: self._redact(value) for key, value in optional_fields.items() if key in self._OPTIONAL_FIELDS})
        if capability_probe is not None:
            trace["capability_probe"] = self._redact(dict(capability_probe))
        self._fault("llm_trace_before_append")
        self.store.append_llm_trace(trace)
        self._fault("llm_trace_appended")
        return trace

    def publish_failure(
        self,
        *,
        provider_name: str,
        request: Any,
        error: BaseException,
        context: Any | None = None,
        latency_ms: int = 0,
        attempts: int = 1,
        responses: list[Any] | None = None,
        provider_requests: list[Any] | None = None,
        repair_attempts: int = 0,
        capability_probe: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        sequence = self.store.next_llm_call_sequence()
        requests = provider_requests or [request]
        prompt_refs = [self._publish_prompt(sequence, "" if index == 0 else f".repair{index}", item) for index, item in enumerate(requests)]
        prompt_ref = prompt_refs[0]
        response_refs = [
            self._publish_output(sequence, "" if index == 0 else f".repair{index}", item)
            for index, item in enumerate(responses or [])
        ]
        output_ref = self._publish_failure_output(sequence, error, ".failure" if response_refs else "")
        primary_prompt = self.store.read_verified_bytes(prompt_ref.path, prompt_ref.sha256)
        last_response = (responses or [None])[-1]
        trace = {
            "ts": self._timestamp(),
            "run_id": getattr(context, "run_id", self.store.run_id),
            "stage": getattr(context, "stage", "llm"),
            "agent_role": request.role,
            "tier": getattr(context, "tier", "unknown"),
            "task_id": getattr(context, "task_id", None),
            "attempt": getattr(context, "attempt", 1),
            "model": f"{provider_name}/{last_response.model if last_response is not None else 'unknown'}",
            "params_requested": {"temperature": request.temperature, "max_tokens": request.max_tokens},
            "parameter_support": {
                key: value.value if hasattr(value, "value") else value
                for key, value in (last_response.parameter_support.items() if last_response is not None else {"temperature": "unknown"}.items())
            },
            "prompt_sha256": hashlib.sha256(primary_prompt).hexdigest(),
            "prompt_path": prompt_ref.path,
            "output_path": output_ref.path,
            "tokens_in": sum(item.tokens_in for item in (responses or [])),
            "tokens_out": sum(item.tokens_out for item in (responses or [])),
            "cost_usd": sum(item.cost_usd for item in (responses or [])),
            "latency_ms": latency_ms,
            "validation": "fail",
            "cached": False,
            "transport_attempts": attempts,
            "repair_attempts": repair_attempts,
            "error": self._redact(str(error)),
            "provider_prompt_paths": [ref.path for ref in prompt_refs],
            "provider_output_paths": [ref.path for ref in response_refs],
        }
        if context is not None and "prompt_template_sha256" in context.trace_fields:
            trace["prompt_template_sha256"] = self._redact(context.trace_fields["prompt_template_sha256"])
        if capability_probe is not None:
            trace["capability_probe"] = self._redact(dict(capability_probe))
        self._fault("llm_trace_before_append")
        self.store.append_llm_trace(trace)
        self._fault("llm_trace_appended")
        return trace
