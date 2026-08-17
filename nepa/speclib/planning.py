"""Pure planning-input and ArchitecturePlanner context preparation.

The functions in this module intentionally operate only on supplied JSON
values.  They do not inspect test files, the workspace, the clock, or a
provider.  This keeps the calibration slice identical to the later S4 input
path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..agents.base import PromptRenderer
from ..agents.roles import get_role
from .lint import BUILTIN_BUILD_VARIANTS, canonical_json_bytes, lint_spec, lint_target, lint_test_bundle


class PlanningError(ValueError):
    """Base error for deterministic planning preparation."""

    def __init__(self, message: str, *, code: str = "PLANNING_INPUT_INVALID") -> None:
        self.code = code
        super().__init__(message)


class PlanningInputError(PlanningError):
    pass


class PlanningContextError(PlanningError):
    pass


@dataclass(frozen=True)
class PreparedArchitectureInputs:
    spec: dict[str, Any]
    target_profile: dict[str, Any]
    test_bundle: dict[str, Any]
    spec_bytes: bytes
    target_bytes: bytes
    test_bundle_bytes: bytes
    spec_sha256: str
    target_sha256: str
    test_bundle_sha256: str

    @property
    def hashes(self) -> dict[str, str]:
        return {
            "spec": self.spec_sha256,
            "target": self.target_sha256,
            "test_bundle": self.test_bundle_sha256,
        }

    @property
    def target(self) -> dict[str, Any]:
        return self.target_profile

    @property
    def spec_raw_bytes(self) -> bytes:
        return self.spec_bytes

    @property
    def target_profile_bytes(self) -> bytes:
        return self.target_bytes

    @property
    def test_bundle_raw_bytes(self) -> bytes:
        return self.test_bundle_bytes


def _source(source: Any, label: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(source, bytes):
        try:
            raw = bytes(source)
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlanningInputError(f"unable to read {label}: {exc}") from exc
        if not isinstance(value, dict):
            raise PlanningInputError(f"{label} must contain a JSON object")
        return value, raw
    if isinstance(source, Mapping):
        value = dict(source)
        try:
            return value, canonical_json_bytes(value)
        except (TypeError, ValueError) as exc:
            raise PlanningInputError(f"{label} is not canonical JSON: {exc}") from exc
    try:
        raw = Path(source).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningInputError(f"unable to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanningInputError(f"{label} must contain a JSON object")
    return value, raw


def _require(report: Mapping[str, Any], label: str) -> None:
    if report.get("valid"):
        return
    errors = report.get("errors", [])
    detail = "; ".join(f"{item.get('code')}: {item.get('message')}" for item in errors)
    code = errors[0].get("code", "PLANNING_INPUT_INVALID") if errors else "PLANNING_INPUT_INVALID"
    raise PlanningInputError(f"{label} failed validation: {detail}", code=code)


def prepare_architecture_inputs(
    spec: Any,
    target_profile: Any = None,
    test_bundle: Any = None,
    *,
    target: Any = None,
) -> PreparedArchitectureInputs:
    """Validate and freeze the three semantic S4 inputs in memory.

    Spec bytes are preserved exactly as supplied.  Target bytes are the
    canonical two-field run copy.  Test Bundle bytes must already be
    canonical, because the bundle is an immutable manifest input.
    """

    if target_profile is None:
        target_profile = target
    if target_profile is None or test_bundle is None:
        raise PlanningInputError("spec, target_profile, and test_bundle are required")
    spec_value, spec_bytes = _source(spec, "Spec IR")
    target_value, _target_source_bytes = _source(target_profile, "Target Profile")
    bundle_value, bundle_bytes = _source(test_bundle, "Test Bundle")
    _require(lint_spec(spec_value), "Spec IR")
    _require(lint_target(target_value, spec_value), "Target Profile")
    _require(lint_test_bundle(bundle_value, spec_value), "Test Bundle")
    target_bytes = canonical_json_bytes(target_value)
    if bundle_bytes != canonical_json_bytes(bundle_value):
        raise PlanningInputError(
            "Test Bundle input bytes must equal canonical JSON bytes",
            code="TEST_CANONICAL_JSON_NONCANONICAL",
        )
    return PreparedArchitectureInputs(
        spec=spec_value,
        target_profile=target_value,
        test_bundle=bundle_value,
        spec_bytes=spec_bytes,
        target_bytes=target_bytes,
        test_bundle_bytes=bundle_bytes,
        spec_sha256=hashlib.sha256(spec_bytes).hexdigest(),
        target_sha256=hashlib.sha256(target_bytes).hexdigest(),
        test_bundle_sha256=hashlib.sha256(bundle_bytes).hexdigest(),
    )


def _reparse_prepared(prepared: PreparedArchitectureInputs) -> PreparedArchitectureInputs:
    """Use the frozen source bytes, never the caller-owned projection mappings."""

    for value, expected, label in (
        (prepared.spec_bytes, prepared.spec_sha256, "spec"),
        (prepared.target_bytes, prepared.target_sha256, "target"),
        (prepared.test_bundle_bytes, prepared.test_bundle_sha256, "test bundle"),
    ):
        if hashlib.sha256(value).hexdigest() != expected:
            raise PlanningInputError(f"{label} frozen-byte hash does not match its PreparedArchitectureInputs identity")
    return prepare_architecture_inputs(prepared.spec_bytes, prepared.target_bytes, prepared.test_bundle_bytes)


def _ordered_variants(test: Mapping[str, Any], default: list[str], allowed: set[str]) -> list[str]:
    variants = test.get("build_variant_ids", default)
    if not isinstance(variants, list) or not variants:
        raise PlanningInputError("test build_variant_ids must be non-empty", code="TEST_BUILD_VARIANT_INVALID")
    if any(not isinstance(item, str) or item not in allowed for item in variants):
        raise PlanningInputError("test uses an unsupported build variant", code="TEST_BUILD_VARIANT_UNSUPPORTED")
    return sorted(set(variants))


def build_test_manifest_metadata(bundle: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Any]:
    """Project only test metadata visible to S4 planning roles."""

    if not isinstance(bundle, Mapping) or not isinstance(bundle.get("bundle"), Mapping):
        raise PlanningInputError("test bundle must be a validated object")
    default = list(bundle["bundle"].get("default_build_variant_ids", []))
    available = set(constraints.get("build_variant_ids", default))
    tests: list[dict[str, Any]] = []
    for test in bundle.get("tests", []):
        if not isinstance(test, Mapping):
            raise PlanningInputError("test bundle contains a non-object test")
        tests.append(
            {
                "nodeid": test["nodeid"],
                "layer": test["layer"],
                "description": test["description"],
                "req_ids": sorted(test["req_ids"]),
                "gate": test["gate"],
                "build_variant_ids": _ordered_variants(test, default, available),
            }
        )
    tests.sort(key=lambda item: item["nodeid"].encode("utf-8"))
    # The complete build-variant index comes from the fixed language rule.  It
    # is not inferred from whichever tests happen to mention a variant.
    variants = sorted(BUILTIN_BUILD_VARIANTS)
    return {
        "schema_version": "1.0",
        "bundle": {"id": bundle["bundle"]["id"], "version": bundle["bundle"]["version"]},
        "tests": tests,
        "build_variant_ids": variants,
    }


def _without_source(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_source(child)
            for key, child in value.items()
            if key != "source_ref"
        }
    if isinstance(value, list):
        return [_without_source(item) for item in value]
    return value


def _normalize_structural(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _normalize_structural(child) for key, child in value.items() if key != "source_ref"}
        for key in ("req_ids", "senders", "receivers"):
            if key in result and isinstance(result[key], list):
                result[key] = sorted(result[key], key=lambda item: str(item).encode("utf-8"))
        return result
    if isinstance(value, list):
        return [_normalize_structural(item) for item in value]
    return value


def _type_dependencies(type_def: Mapping[str, Any]) -> list[str]:
    encoding = type_def.get("encoding", {})
    refs: list[str] = []
    if encoding.get("kind") == "sequence":
        for member in encoding.get("members", []):
            refs.append(member if isinstance(member, str) else member.get("type"))
    elif encoding.get("kind") == "repeat":
        refs.append(encoding.get("item_type"))
    return sorted({item for item in refs if isinstance(item, str)})


def build_planning_index(
    prepared: PreparedArchitectureInputs,
    manifest_metadata: Mapping[str, Any],
    constraints: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the closed, source-quote-free planning projection."""

    if not isinstance(prepared, PreparedArchitectureInputs):
        raise PlanningInputError("prepared must be PreparedArchitectureInputs")
    frozen = _reparse_prepared(prepared)
    from .delivery import compile_delivery_constraints

    expected_constraints = compile_delivery_constraints(frozen.spec, frozen.target_profile)
    expected_manifest = build_test_manifest_metadata(frozen.test_bundle, expected_constraints)
    if dict(constraints) != expected_constraints or dict(manifest_metadata) != expected_manifest:
        raise PlanningInputError("planning parents do not match the reparsed frozen inputs")
    prepared = frozen
    spec = prepared.spec
    requirements = {item["id"]: item for item in spec["requirements"]}
    type_ids = {item["id"] for item in spec["types"]}
    message_ids = {item["id"] for item in spec["messages"]}
    known_ids = type_ids | message_ids
    def check_refs(refs: Any, owner: str) -> list[str]:
        if not isinstance(refs, list):
            raise PlanningInputError(f"{owner} req_ids must be an array", code="PLANNING_REFERENCE_UNKNOWN")
        result = sorted(set(refs))
        unknown = [item for item in result if item not in requirements]
        if unknown:
            raise PlanningInputError(f"{owner} references unknown requirement {unknown[0]!r}", code="PLANNING_REFERENCE_UNKNOWN")
        return result

    types: list[dict[str, Any]] = []
    type_graph: dict[str, list[str]] = {}
    element_requirements: dict[str, list[str]] = {}
    for type_def in spec["types"]:
        type_id = type_def["id"]
        deps = _type_dependencies(type_def)
        unknown = [item for item in deps if item not in known_ids and item not in {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}]
        if unknown:
            raise PlanningInputError(f"type {type_id!r} references unknown type {unknown[0]!r}", code="PLANNING_REFERENCE_UNKNOWN")
        req_ids = check_refs(type_def["req_ids"], f"type {type_id}")
        types.append(_normalize_structural(type_def))
        type_graph[type_id] = deps
        element_requirements[f"type:{type_id}"] = req_ids

    messages: list[dict[str, Any]] = []
    message_graph: dict[str, dict[str, Any]] = {}
    for message in spec["messages"]:
        message_id = message["id"]
        req_ids = check_refs(message["req_ids"], f"message {message_id}")
        field_graph: dict[str, list[str]] = {}
        for field in message.get("fields", []):
            field_type = field["type"]
            if field_type not in known_ids and field_type not in {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}:
                raise PlanningInputError(f"field references unknown type {field_type!r}", code="PLANNING_REFERENCE_UNKNOWN")
            field_key = field["name"]
            field_graph[field_key] = [field_type]
            element_requirements[f"field:{message_id}:{field_key}"] = check_refs(field["req_ids"], f"field {message_id}.{field_key}")
        messages.append(_normalize_structural(message))
        message_graph[message_id] = {"types": sorted({item for values in field_graph.values() for item in values}), "fields": field_graph}
        element_requirements[f"message:{message_id}"] = req_ids
    if "transport" in spec:
        element_requirements["transport"] = check_refs(spec["transport"]["req_ids"], "transport")
    requirements_projection = [
        {"id": item["id"], "level": item["level"], "text": item["text"]}
        for item in sorted(spec["requirements"], key=lambda item: item["id"].encode("utf-8"))
    ]
    return {
        "schema_version": "1.0",
        "protocol": {
            "name": spec["protocol"]["name"],
            "version": spec["protocol"]["version"],
            "roles": sorted(spec["protocol"]["roles"]),
        },
        "target_profile": {
            "roles": sorted(prepared.target_profile["roles"]),
            "language": dict(prepared.target_profile["language"]),
        },
        "transport": _without_source(spec.get("transport")) if "transport" in spec else None,
        "types": sorted(types, key=lambda item: item["id"].encode("utf-8")),
        "messages": sorted(messages, key=lambda item: item["id"].encode("utf-8")),
        "requirements": requirements_projection,
        "reference_graph": {
            "type_dependencies": {key: type_graph[key] for key in sorted(type_graph)},
            "message_dependencies": {key: message_graph[key] for key in sorted(message_graph)},
            "element_requirements": {key: element_requirements[key] for key in sorted(element_requirements)},
        },
        "tests": list(manifest_metadata.get("tests", [])),
        "build_variant_ids": list(manifest_metadata.get("build_variant_ids", constraints.get("build_variant_ids", []))),
    }


def preflight_architecture_planner_context(
    rendered_prompt: str,
    *,
    model_limits: Mapping[str, int],
    requested_output_tokens: int,
    safety_margin_ratio: float,
    system_prompt: str = "",
) -> dict[str, Any]:
    """Reject an over-sized ArchitecturePlanner request before provider I/O."""

    if not isinstance(rendered_prompt, str):
        raise PlanningContextError("rendered prompt must be text", code="PLAN_CONTEXT_INVALID")
    if not isinstance(requested_output_tokens, int) or requested_output_tokens <= 0:
        raise PlanningContextError("requested output reserve must be positive", code="PLAN_CONTEXT_INVALID")
    if not 0 <= safety_margin_ratio <= 1:
        raise PlanningContextError("context safety margin must be between 0 and 1", code="PLAN_CONTEXT_INVALID")
    if not isinstance(system_prompt, str):
        raise PlanningContextError("system prompt must be text", code="PLAN_CONTEXT_INVALID")
    input_bound = len((system_prompt + "\n" + rendered_prompt).encode("utf-8"))
    effective: dict[str, int] = {}
    for model_id, limit in sorted(model_limits.items()):
        if not isinstance(limit, int) or limit <= 0:
            raise PlanningContextError(f"missing or invalid context limit for {model_id}", code="PLAN_CONTEXT_LIMIT_MISSING")
        effective[model_id] = int(limit * (1 - safety_margin_ratio))
        if input_bound + requested_output_tokens > effective[model_id]:
            raise PlanningContextError(
                f"ArchitecturePlanner context exceeds {model_id}: {input_bound}+{requested_output_tokens}>{effective[model_id]}",
                code="PLAN_CONTEXT_TOO_LARGE",
            )
    return {
        "input_byte_upper_bound": input_bound,
        "requested_output_tokens": requested_output_tokens,
        "safety_margin_ratio": safety_margin_ratio,
        "effective_boundaries": effective,
    }


def architecture_planner_context_preflight(
    planning_index: Mapping[str, Any],
    delivery_constraints: Mapping[str, Any],
    *,
    model_limits: Mapping[str, int],
    requested_output_tokens: int,
    safety_margin_ratio: float,
    output_schema: dict[str, Any] | None = None,
    output_example: Any | None = None,
    repair_context: Any = None,
) -> dict[str, Any]:
    """Render the production-shaped request and apply the byte-bound check."""

    from ..agents.base import AGENT_SYSTEM_INSTRUCTION
    from ..schemas import architecture_draft_contract

    schema = output_schema or architecture_draft_contract()[0]
    example = output_example if output_example is not None else architecture_draft_contract()[1]
    rendered = PromptRenderer.render(
        get_role("architecture_planner"),
        inputs={"planning_index": planning_index, "delivery_constraints": delivery_constraints, "repair_context": repair_context},
        output_schema=schema,
        output_example=example,
    )
    return preflight_architecture_planner_context(
        rendered.user,
        model_limits=model_limits,
        requested_output_tokens=requested_output_tokens,
        safety_margin_ratio=safety_margin_ratio,
        system_prompt=AGENT_SYSTEM_INSTRUCTION,
    )


def check_architecture_planner_context(
    prompt: str,
    *,
    context_window_tokens: Mapping[str, int],
    max_output_tokens: int,
    safety_margin_ratio: float,
) -> dict[str, Any]:
    """Named compatibility entry point for the S4 context-boundary check."""

    return preflight_architecture_planner_context(
        prompt,
        model_limits=context_window_tokens,
        requested_output_tokens=max_output_tokens,
        safety_margin_ratio=safety_margin_ratio,
    )


preflight_architecture_context = check_architecture_planner_context


class ArchitecturePlanner:
    """Small deterministic preflight facade used by production and calibration."""

    def __init__(self, *, model_limits: Mapping[str, int], requested_output_tokens: int, safety_margin_ratio: float) -> None:
        self.model_limits = dict(model_limits)
        self.requested_output_tokens = requested_output_tokens
        self.safety_margin_ratio = safety_margin_ratio

    def preflight(self, prompt: str) -> dict[str, Any]:
        return preflight_architecture_planner_context(
            prompt,
            model_limits=self.model_limits,
            requested_output_tokens=self.requested_output_tokens,
            safety_margin_ratio=self.safety_margin_ratio,
        )


__all__ = [
    "ArchitecturePlanner", "PreparedArchitectureInputs", "PlanningContextError", "PlanningError", "PlanningInputError",
    "architecture_planner_context_preflight", "build_planning_index", "build_test_manifest_metadata",
    "check_architecture_planner_context", "preflight_architecture_context",
    "prepare_architecture_inputs", "preflight_architecture_planner_context",
]
