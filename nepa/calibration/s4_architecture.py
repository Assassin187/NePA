"""Hash-bound ArchitecturePlanner calibration infrastructure.

This module deliberately owns only the isolated experiment.  The deterministic
planning and validation functions are imported from :mod:`nepa.speclib` so the
experiment cannot silently acquire a second semantic contract.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from ..agents.base import AGENT_SYSTEM_INSTRUCTION, AgentInvoker, AgentResult, PromptRenderer
from ..agents.roles import get_role
from ..config import ResolvedConfig, load_config
from ..llm.client import DecodingError, LLMClient, LLMRequest, LLMResponse, StructuredOutputError, TransportError, ProviderError, extract_first_json_value, structured_validation_errors
from ..llm.telemetry import LLMTelemetry
from ..run_store import ArtifactRef, RunStore, RunStoreError
from ..schemas import architecture_draft_contract, load_schema
from ..speclib.architecture import serialize_architecture_draft, validate_architecture
from ..speclib.delivery import canonical_layout_convention, compile_delivery_constraints
from ..speclib.lint import canonical_json_bytes
from ..speclib.planning import (
    PreparedArchitectureInputs,
    architecture_planner_context_preflight,
    build_planning_index,
    build_test_manifest_metadata,
    prepare_architecture_inputs,
)


MODEL_IDS = ("qwen", "claude", "deepseek")
FIXED_API_KEY_ENVS = {
    "qwen": "NEPA_QWEN_API_KEY",
    "claude": "NEPA_CLAUDE_API_KEY",
    "deepseek": "NEPA_DS_API_KEY",
}
METRIC_DEFINITION = "m1-4a1-architecture-calibration-metrics-v1"
RECOVERY_METRIC_DEFINITION = "m1-4a2r-recovery-metrics-v1"
REPAIR_IMPACT_POLICY_VERSION = "repair-impact-v1"
DESIGN_BASELINE = {
    "project_docs/system_design.md": "49ec7593600b09a1d6c7ea7d748b9e8843eaa77c7e86e6cdb4784f720227d8e8",
    "project_docs/pipeline_design_s4_s9.md": "6ebf3c693e519fd14229b3591b4226d51c9df399380f28164d5d981d45c51af8",
}

# Closed gate dependency policy used only to audit the existing full-draft
# semantic repair.  It does not change ARCH_VALIDATE or make an invalid draft
# acceptable.  Prefixes use the stable-id form produced by
# ``architecture_draft_changed_paths``.
REPAIR_IMPACT_POLICY: dict[str, tuple[str, ...]] = {
    "arch_01": ("/decisions", "/contracts", "/modules", "/work_packages", "/layout"),
    "arch_02": ("/modules", "/work_packages", "/layout"),
    "arch_03": ("/contracts", "/modules", "/work_packages", "/layout"),
    "arch_04": ("/contracts", "/modules", "/work_packages"),
    "arch_05": ("/contracts", "/modules", "/work_packages", "/layout"),
    "arch_06": ("/modules", "/work_packages", "/layout"),
    "arch_07": ("/contracts", "/modules", "/work_packages", "/layout"),
    "arch_08": ("/contracts", "/work_packages", "/layout"),
    "arch_09": ("/contracts", "/modules", "/work_packages", "/layout"),
    "arch_10": ("/work_packages",),
    "arch_11": ("/layout",),
    "arch_12": ("/layout", "/modules", "/work_packages"),
    "arch_13": ("/layout",),
    "arch_14": ("/layout", "/contracts", "/modules"),
    "arch_15": ("/layout",),
}


def recovery_component_bytes() -> bytes:
    return canonical_json_bytes({
        "metric_definition": RECOVERY_METRIC_DEFINITION,
        "repair_policy_version": REPAIR_IMPACT_POLICY_VERSION,
        "repair_policy": REPAIR_IMPACT_POLICY,
        "quality_audit": "m1-4a2r-quality-audit-v1",
    })


def _json_pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _stable_array_key(path: str, item: object) -> str | None:
    if not isinstance(item, Mapping):
        return None
    if path in {"/decisions", "/contracts", "/modules", "/work_packages"}:
        value = item.get("id")
        return str(value) if isinstance(value, str) else None
    if path == "/layout/files":
        value = item.get("slot_id")
        return str(value) if isinstance(value, str) else None
    if path == "/layout/build_graph/artifacts":
        value = item.get("artifact_id")
        return str(value) if isinstance(value, str) else None
    if path.endswith("/requirement_responsibilities"):
        value = item.get("req_id")
        return str(value) if isinstance(value, str) else None
    if path.endswith("/context_refs"):
        kind, value = item.get("kind"), item.get("id")
        return f"{kind}:{value}" if isinstance(kind, str) and isinstance(value, str) else None
    return None


def architecture_draft_changed_paths(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Return canonical semantic JSON Pointer paths for a draft repair.

    Contract arrays with stable identifiers are compared as keyed sets, so a
    harmless reordering does not become a repair. Other arrays retain ordinary
    positional semantics because their order/duplicates can carry meaning.
    """

    changed: set[str] = set()

    def walk(left: object, right: object, path: str) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in sorted(set(left) | set(right), key=lambda item: str(item).encode("utf-8")):
                child = f"{path}/{_json_pointer_token(key)}"
                if key not in left or key not in right:
                    changed.add(child)
                else:
                    walk(left[key], right[key], child)
            return
        if isinstance(left, list) and isinstance(right, list):
            keyed_left = {_stable_array_key(path, item): item for item in left}
            keyed_right = {_stable_array_key(path, item): item for item in right}
            keyed = (
                len(keyed_left) == len(left)
                and len(keyed_right) == len(right)
                and None not in keyed_left
                and None not in keyed_right
            )
            if keyed:
                for key in sorted(set(keyed_left) | set(keyed_right), key=lambda item: str(item).encode("utf-8")):
                    child = f"{path}/{_json_pointer_token(key)}"
                    if key not in keyed_left or key not in keyed_right:
                        changed.add(child)
                    else:
                        walk(keyed_left[key], keyed_right[key], child)
                return
            for index in range(max(len(left), len(right))):
                child = f"{path}/{index}"
                if index >= len(left) or index >= len(right):
                    changed.add(child)
                else:
                    walk(left[index], right[index], child)
            return
        if left != right:
            changed.add(path or "/")

    walk(before, after, "")
    return sorted(changed, key=lambda item: item.encode("utf-8"))


def repair_impact_closure(issues: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Compute the closed issue-to-path attribution policy for one repair."""

    result: dict[str, set[str]] = {}
    for issue in issues:
        gate = issue.get("gate") or issue.get("gate_id")
        code = issue.get("code")
        path = issue.get("path")
        if gate not in REPAIR_IMPACT_POLICY:
            raise CalibrationEvidenceError(f"repair issue has unsupported gate: {gate!r}")
        issue_key = f"{gate}:{code}" if isinstance(code, str) and code else str(gate)
        prefixes = result.setdefault(issue_key, set(REPAIR_IMPACT_POLICY[str(gate)]))
        if isinstance(path, str) and path.startswith("/"):
            prefixes.add(path)
        if code == "ARCH_TEST_READINESS_UNCLOSED":
            prefixes.update(("/work_packages", "/work_packages/*/depends_on", "/work_packages/*/requirement_responsibilities"))
    return {key: sorted(values, key=lambda item: item.encode("utf-8")) for key, values in sorted(result.items())}


def assess_repair_locality(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    issues: list[Mapping[str, Any]],
    before_validation: Mapping[str, Any],
    after_validation: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish the deterministic repair diff, attribution and regression gate."""

    changed = architecture_draft_changed_paths(before, after)
    closure = repair_impact_closure(issues)

    def admitted(path: str, prefix: str) -> bool:
        if "/*/" in prefix:
            start, end = prefix.split("/*/", 1)
            return path.startswith(start + "/") and ("/" + end) in path[len(start):]
        return path == prefix or path.startswith(prefix.rstrip("/") + "/")

    attribution = {
        path: [key for key, prefixes in closure.items() if any(admitted(path, prefix) for prefix in prefixes)]
        for path in changed
    }
    before_gates = {item.get("id"): item.get("verdict") for item in before_validation.get("gates", []) if isinstance(item, Mapping)}
    after_gates = {item.get("id"): item.get("verdict") for item in after_validation.get("gates", []) if isinstance(item, Mapping)}
    improved = sorted(gate for gate in before_gates if before_gates[gate] == "fail" and after_gates.get(gate) == "pass")
    regressed = sorted(gate for gate in before_gates if before_gates[gate] == "pass" and after_gates.get(gate) == "fail")
    unchanged = sorted(gate for gate in before_gates if before_gates[gate] == after_gates.get(gate))
    unattributed = [path for path, keys in attribution.items() if not keys]
    passed = bool(changed) and not unattributed and not regressed
    return {
        "schema_version": "2.0",
        "policy_version": REPAIR_IMPACT_POLICY_VERSION,
        "before_sha256": _sha(canonical_json_bytes(before)),
        "after_sha256": _sha(canonical_json_bytes(after)),
        "changed_paths": changed,
        "impact_closure": closure,
        "attribution": attribution,
        "unattributed_paths": unattributed,
        "improved_gates": improved,
        "unchanged_gates": unchanged,
        "regressed_gates": regressed,
        "locality_pass": passed,
    }


class CalibrationError(RuntimeError):
    pass


class CalibrationDeclarationError(CalibrationError):
    pass


class CalibrationEvidenceError(CalibrationError):
    pass


def verify_design_baseline(workspace_root: str | Path | None = None) -> dict[str, str]:
    """Verify the owner-resolved design bytes before calibration preparation."""

    root = Path(workspace_root).resolve() if workspace_root is not None else Path(__file__).resolve().parents[2]
    verified: dict[str, str] = {}
    for relative, expected in DESIGN_BASELINE.items():
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
            data = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise CalibrationDeclarationError(f"design baseline is unavailable: {relative}") from exc
        actual = _sha(data)
        if actual != expected:
            raise CalibrationDeclarationError(f"design baseline drift: {relative}")
        verified[relative] = actual
    return verified


@dataclass(frozen=True)
class CalibrationModelTarget:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    context_window_tokens: int

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip() or self.max_tokens != 65536 or self.context_window_tokens <= 0 or self.temperature < 0:
            raise CalibrationDeclarationError("model target has invalid provider, model, temperature, token, or context values")

    @classmethod
    def from_value(cls, value: Any) -> "CalibrationModelTarget":
        if isinstance(value, cls):
            return value
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if not isinstance(value, Mapping):
            raise CalibrationDeclarationError("model target must be an object")
        try:
            return cls(
                provider=str(value["provider"]), model=str(value["model"]),
                temperature=float(value.get("temperature", 0)), max_tokens=int(value["max_tokens"]),
                context_window_tokens=int(value.get("context_window_tokens", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CalibrationDeclarationError(f"invalid model target: {exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model, "temperature": self.temperature,
            "max_tokens": self.max_tokens, "context_window_tokens": self.context_window_tokens,
        }


@dataclass(frozen=True)
class CalibrationBatchDeclaration:
    prompt_version: str = "v0"
    trial_count: int = 1
    semantic_repair_depth: int = 0
    context_window_tokens: Mapping[str, int] = field(default_factory=dict)
    models: Mapping[str, Any] | None = None
    spec: Any | None = None
    target_profile: Any | None = None
    test_bundle: Any | None = None
    prepared_inputs: PreparedArchitectureInputs | None = None
    prompt_sha256: str | None = None
    fault_hook: Callable[[str], None] | None = None
    max_semantic_repairs: int | None = None
    attempt: int = 1
    batch_kind: str = "base"
    trial_start: int = 1
    trial_ids: tuple[str, ...] | None = None
    root_relative_path: str | None = None

    @property
    def semantic_depth(self) -> int:
        return self.semantic_repair_depth if self.max_semantic_repairs is None else self.max_semantic_repairs

    def targets(self, config: ResolvedConfig) -> dict[str, CalibrationModelTarget]:
        values = self.models or config.calibration_models
        if set(values) != set(MODEL_IDS):
            raise CalibrationDeclarationError(f"calibration models must be exactly {MODEL_IDS}")
        targets: dict[str, CalibrationModelTarget] = {}
        for model_id in MODEL_IDS:
            raw_target = values[model_id]
            if hasattr(raw_target, "model_dump"):
                raw_target = raw_target.model_dump(mode="json")
            if isinstance(raw_target, Mapping):
                raw_target = dict(raw_target)
                if self.context_window_tokens and model_id in self.context_window_tokens:
                    raw_target["context_window_tokens"] = self.context_window_tokens[model_id]
            target = CalibrationModelTarget.from_value(raw_target)
            context_limit = self.context_window_tokens.get(model_id) if self.context_window_tokens else target.context_window_tokens
            if context_limit is None or int(context_limit) <= 0:
                raise CalibrationDeclarationError(f"context window is missing for {model_id}")
            targets[model_id] = CalibrationModelTarget(
                provider=target.provider, model=target.model, temperature=target.temperature,
                max_tokens=target.max_tokens, context_window_tokens=int(context_limit),
            )
            if target.provider not in config.providers:
                raise CalibrationDeclarationError(f"provider is not configured for {model_id}: {target.provider}")
            if f"{target.provider}/{target.model}" not in config.pricing.models:
                raise CalibrationDeclarationError(f"pricing is not configured for {model_id}: {target.provider}/{target.model}")
        semantic_depth = self.semantic_repair_depth if self.max_semantic_repairs is None else self.max_semantic_repairs
        if self.trial_count <= 0 or semantic_depth not in {0, 1, 2}:
            raise CalibrationDeclarationError("trial_count must be positive and semantic depth must be 0, 1, or 2")
        _validate_prompt_version(self.prompt_version)
        if self.attempt <= 0:
            raise CalibrationDeclarationError("attempt must be positive")
        if self.batch_kind not in {"base", "extension"}:
            raise CalibrationDeclarationError("batch_kind must be base or extension")
        if self.trial_start <= 0:
            raise CalibrationDeclarationError("trial_start must be positive")
        names = self.trial_ids or tuple(f"trial_{index:03d}" for index in range(self.trial_start, self.trial_start + self.trial_count))
        if len(names) != self.trial_count or len(set(names)) != len(names) or any(re.fullmatch(r"trial_[0-9]{3}", name) is None for name in names):
            raise CalibrationDeclarationError("trial_ids must be a unique fixed sample matching trial_count")
        if self.root_relative_path is not None:
            if self.root_relative_path.startswith("/") or "\\" in self.root_relative_path or ".." in Path(self.root_relative_path).parts:
                raise CalibrationDeclarationError("root_relative_path must be confined below the lineage root")
        return targets

    def trial_names(self) -> tuple[str, ...]:
        return self.trial_ids or tuple(
            f"trial_{index:03d}" for index in range(self.trial_start, self.trial_start + self.trial_count)
        )


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


_SAFE_PROMPT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_prompt_version(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_PROMPT_VERSION.fullmatch(value) is None or value in {".", ".."}:
        raise CalibrationDeclarationError("prompt_version must be a safe non-empty single path segment")
    return value


def _confined_path(root: Path, *parts: str) -> Path:
    candidate = (root / Path(*parts)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CalibrationEvidenceError("calibration path escapes its lineage root") from exc
    return candidate


def _ref(path: str, data: bytes) -> dict[str, str]:
    return {"path": path, "sha256": _sha(data)}


def _source_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_json_bytes(value)


def _component_bytes(component: Any) -> bytes:
    if isinstance(component, Path):
        return component.read_bytes()
    if isinstance(component, str):
        candidate = Path(component)
        if candidate.exists() and candidate.is_file():
            return candidate.read_bytes()
    return _source_bytes(component)


_CONTROLLED_COMPONENT_FILES: dict[str, tuple[str, ...]] = {
    "serializer": (
        "nepa/schemas/__init__.py",
        "nepa/schemas/architecture-draft.schema.json",
        "nepa/speclib/architecture.py",
        "nepa/speclib/lint.py",
    ),
    "planning": (
        "nepa/speclib/lint.py",
        "nepa/speclib/planning.py",
    ),
    "delivery": (
        "nepa/speclib/delivery.py",
        "nepa/speclib/lint.py",
    ),
    "validator": (
        "nepa/schemas/__init__.py",
        "nepa/schemas/architecture-draft.schema.json",
        "nepa/schemas/architecture-validation.schema.json",
        "nepa/speclib/architecture.py",
        "nepa/speclib/lint.py",
    ),
    "agent_framework": (
        "nepa/agents/base.py",
        "nepa/agents/roles.py",
        "nepa/speclib/lint.py",
    ),
    "llm_runtime": (
        "nepa/config.py",
        "nepa/llm/cache.py",
        "nepa/llm/client.py",
        "nepa/speclib/lint.py",
    ),
    "telemetry": (
        "nepa/llm/telemetry.py",
        "nepa/run_store.py",
        "nepa/speclib/lint.py",
    ),
    "provider_adapters": (
        "nepa/llm/providers/anthropic.py",
        "nepa/llm/providers/openai_compat.py",
        "nepa/llm/providers/__init__.py",
    ),
    "statistics": (
        "nepa/calibration/s4_architecture.py",
        "nepa/calibration/s4_prompt_development.py",
        "nepa/schemas/calibration-development-protocol.schema.json",
        "nepa/schemas/calibration-prompt-version.schema.json",
        "nepa/schemas/calibration-prompt-snapshot.schema.json",
        "nepa/schemas/calibration-prompt-revision.schema.json",
        "nepa/schemas/calibration-attempt-declaration.schema.json",
        "nepa/schemas/calibration-attempt-outcome.schema.json",
        "nepa/schemas/calibration-development-extension.schema.json",
        "nepa/schemas/calibration-development-assessment.schema.json",
        "nepa/schemas/calibration-development-outcome.schema.json",
        "nepa/schemas/calibration-development-selection.schema.json",
    ),
}


def _source_bundle_bytes(paths: tuple[str, ...] | list[str]) -> bytes:
    """Return one deterministic, auditable bundle of controlled source files."""

    root = Path(__file__).resolve().parents[2]
    files: list[dict[str, str]] = []
    for relative in sorted(paths, key=lambda item: item.encode("utf-8")):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise CalibrationDeclarationError(f"controlled source path is unsafe: {relative}")
        raw = (root / path).read_bytes()
        files.append({
            "path": relative,
            "sha256": _sha(raw),
            "bytes_base64": base64.b64encode(raw).decode("ascii"),
        })
    return canonical_json_bytes({"format": "nepa-controlled-source-bundle-v1", "files": files})


def _default_components() -> dict[str, bytes]:
    return {
        name: _source_bundle_bytes(paths)
        for name, paths in sorted(_CONTROLLED_COMPONENT_FILES.items())
    }


def _component_values(
    components: Mapping[str, Any] | None = None,
    component_bytes: Mapping[str, Any] | None = None,
) -> dict[str, bytes]:
    values = _default_components()
    for name, value in {**(components or {}), **(component_bytes or {})}.items():
        # Prompt identity is intentionally recorded by prompt-version evidence,
        # but is the sole within-lineage variable and therefore excluded here.
        if name in {"prompt", "prompt_label", "prompt_hash", "prompt_sha256"}:
            continue
        if name not in values:
            raise CalibrationDeclarationError(f"unknown lineage component: {name}")
        values[name] = _component_bytes(value)
    return values


def _component_path(name: str) -> str:
    return f"components/{name}.bundle.json"


def build_lineage_manifest(
    prepared: PreparedArchitectureInputs,
    planning_index: Mapping[str, Any],
    manifest_metadata: Mapping[str, Any],
    delivery_constraints: Mapping[str, Any],
    *,
    config: ResolvedConfig | None = None,
    model_targets: Mapping[str, Any] | None = None,
    context_window_tokens: Mapping[str, int] | None = None,
    schema_bytes: bytes | None = None,
    example_bytes: bytes | None = None,
    components: Mapping[str, Any] | None = None,
    statistics: Mapping[str, Any] | None = None,
    component_bytes: Mapping[str, Any] | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a lineage manifest whose id excludes all prompt identity."""

    if config is None:
        raise CalibrationDeclarationError("lineage construction requires the resolved provider and pricing configuration")
    # Reparse the frozen bytes before deriving any projection.  The public
    # PreparedArchitectureInputs object exposes mappings for callers, so its
    # live mapping identity is never an authority for lineage contents.
    frozen = prepare_architecture_inputs(prepared.spec_bytes, prepared.target_bytes, prepared.test_bundle_bytes)
    expected_constraints = compile_delivery_constraints(frozen.spec, frozen.target_profile)
    convention, convention_sha256 = canonical_layout_convention(frozen.target_profile)
    if expected_constraints.get("layout_convention_sha256") != convention_sha256:
        raise CalibrationDeclarationError("delivery constraints are not bound to the canonical layout convention")
    expected_manifest = build_test_manifest_metadata(frozen.test_bundle, expected_constraints)
    expected_planning = build_planning_index(frozen, expected_manifest, expected_constraints)
    if (
        dict(planning_index) != expected_planning
        or dict(manifest_metadata) != expected_manifest
        or dict(delivery_constraints) != expected_constraints
    ):
        raise CalibrationDeclarationError("derived lineage artifacts do not match reparsed frozen inputs")
    prepared = frozen
    schema = schema_bytes or (Path(__file__).resolve().parents[1] / "schemas/architecture-draft.schema.json").read_bytes()
    example = example_bytes or (Path(__file__).resolve().parents[1] / "schemas/examples/architecture-draft.example.json").read_bytes()
    targets = model_targets or config.calibration_models
    target_projection: dict[str, Any] = {}
    slot_controls: dict[str, Any] = {}
    for model_id in MODEL_IDS:
        if model_id not in targets:
            raise CalibrationDeclarationError(f"missing lineage model target {model_id}")
        target = CalibrationModelTarget.from_value(targets[model_id])
        limit = (context_window_tokens or {}).get(model_id, target.context_window_tokens)
        target_projection[model_id] = {
            "provider": target.provider, "model": target.model, "temperature": target.temperature,
            "max_tokens": target.max_tokens, "context_window_tokens": int(limit),
        }
        slot_controls[model_id] = {
            "provider": target.provider, "temperature": target.temperature,
            "max_tokens": target.max_tokens, "context_window_tokens": int(limit),
        }
    component_values = _component_values(components, component_bytes)
    component_refs = {
        name: _ref(_component_path(name), value)
        for name, value in sorted(component_values.items())
    }
    providers: dict[str, Any] = {}
    pricing: dict[str, Any] = {}
    for provider_name in sorted({target["provider"] for target in target_projection.values()}):
        provider = config.providers.get(provider_name)
        if provider is None:
            raise CalibrationDeclarationError(f"lineage provider is not configured: {provider_name}")
        provider_projection = provider.model_dump(mode="json")
        providers[provider_name] = {
            **provider_projection,
            "sha256": _sha(canonical_json_bytes(provider_projection)),
        }
    for model_id, target in target_projection.items():
        key = f"{target['provider']}/{target['model']}"
        price = config.pricing.models.get(key)
        if price is None:
            raise CalibrationDeclarationError(f"lineage pricing is not configured: {key}")
        pricing[model_id] = price.model_dump(mode="json")
    supplied_statistics = dict(statistics or {})
    forbidden_statistics = {"trial_count", "semantic_depth"}.intersection(supplied_statistics)
    if forbidden_statistics:
        raise CalibrationDeclarationError(
            "lineage statistics cannot contain batch controls: " + ", ".join(sorted(forbidden_statistics))
        )
    unknown_statistics = set(supplied_statistics) - {"metric_definition", "implementation_sha256"}
    if unknown_statistics:
        raise CalibrationDeclarationError(
            "lineage statistics contains unknown fields: " + ", ".join(sorted(unknown_statistics))
        )
    expected_implementation_hash = _sha(Path(__file__).read_bytes())
    semantic_stats = {
        "metric_definition": METRIC_DEFINITION,
        "implementation_sha256": expected_implementation_hash,
    }
    semantic_stats.update(supplied_statistics)
    if semantic_stats["metric_definition"] not in {METRIC_DEFINITION, RECOVERY_METRIC_DEFINITION} or semantic_stats["implementation_sha256"] != expected_implementation_hash:
        raise CalibrationDeclarationError("lineage metric-definition contract or implementation hash drift")
    semantic_stats_bytes = canonical_json_bytes(semantic_stats)
    calibration_projection = {
        "api_key_env": dict(FIXED_API_KEY_ENVS),
        "model_ids": list(MODEL_IDS),
    }
    if calibration is not None:
        supplied_calibration = dict(calibration)
        if set(supplied_calibration) != set(calibration_projection):
            raise CalibrationDeclarationError("lineage calibration projection has unknown or missing fields")
        if supplied_calibration != calibration_projection:
            raise CalibrationDeclarationError("lineage fixed API-key environment-variable mapping drift")
    projection = {
        "schema_version": "2.0",
        "inputs": {
            "spec": _ref("inputs/spec.json", prepared.spec_bytes),
            "target": _ref("inputs/target.json", prepared.target_bytes),
            "test_bundle": _ref("inputs/test_bundle.json", prepared.test_bundle_bytes),
        },
        "artifacts": {
            "planning_index": _ref("planning_index.json", canonical_json_bytes(planning_index)),
            "manifest_metadata": _ref("test_manifest_metadata.json", canonical_json_bytes(manifest_metadata)),
            "delivery_constraints": _ref("delivery_constraints.json", canonical_json_bytes(delivery_constraints)),
            "layout_convention": _ref("layout_convention.json", canonical_json_bytes(convention)),
            "schema": _ref("schema/architecture-draft.schema.json", schema),
            "example": _ref("schema/architecture-draft.example.json", example),
            "serializer": component_refs["serializer"],
            "validator": component_refs["validator"],
        },
        "components": component_refs,
        "providers": providers,
        "slot_controls": slot_controls,
        "models": target_projection,
        "pricing": pricing,
        "calibration": calibration_projection,
        "statistics": {**semantic_stats, "sha256": _sha(semantic_stats_bytes)},
    }
    identity_projection = {key: value for key, value in projection.items() if key != "models"}
    lineage_id = _sha(canonical_json_bytes(identity_projection))
    return {"schema_version": "2.0", "lineage_id": lineage_id, **projection}


def _derived_config(config: ResolvedConfig, target: CalibrationModelTarget) -> ResolvedConfig:
    derived = config.model_copy(deep=True)
    role = derived.roles["architecture_planner"]
    derived.roles["architecture_planner"] = role.model_copy(update={
        "provider": target.provider, "model": target.model,
        "temperature": target.temperature, "max_tokens": target.max_tokens,
    })
    return derived


def _provider_map(factory: Any, model_id: str, target: CalibrationModelTarget, config: ResolvedConfig, store: RunStore) -> Mapping[str, Any]:
    if factory is None:
        from ..llm.providers import AnthropicProvider, OpenAICompatibleProvider
        result = {}
        for name, provider_config in config.providers.items():
            if provider_config.kind == "anthropic":
                result[name] = AnthropicProvider(name, provider_config)
            elif provider_config.kind == "openai_compat":
                result[name] = OpenAICompatibleProvider(name, provider_config)
        return result
    if isinstance(factory, Mapping):
        raise CalibrationDeclarationError("provider mappings are not accepted; use a factory that creates isolated adapters")
    attempts = [
        (model_id, target, config, store), (model_id, target, config), (model_id, config, store),
        (model_id, config), (target, config, store), (target, config), (model_id,), (),
    ]
    last: BaseException | None = None
    for args in attempts:
        try:
            result = factory(*args)
            if isinstance(result, Mapping):
                return result
            if result is not None:
                return {target.provider: result}
        except TypeError as exc:
            last = exc
    raise CalibrationDeclarationError(f"provider factory did not return adapters: {last}")


def _trace_rows(store: RunStore, task_id: str) -> list[dict[str, Any]]:
    path = store.root / "trace/llm_calls.ndjson"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("task_id") == task_id:
                rows.append(row)
    return rows


def _latest_trace(store: RunStore, task_id: str, *, after: int = 0) -> dict[str, Any] | None:
    rows = _trace_rows(store, task_id)
    rows = rows[after:]
    return rows[-1] if rows else None


def _publish_json(store: RunStore, path: str, value: Any, *, schema_name: str | None = None) -> ArtifactRef:
    try:
        return store.publish_immutable_json(path, value, schema_name=schema_name)
    except RunStoreError as exc:
        raise CalibrationEvidenceError(str(exc)) from exc


def _validate_schema_artifact(value: Any, schema_name: str, label: str) -> None:
    errors = sorted(
        Draft202012Validator(load_schema(schema_name)).iter_errors(value),
        key=lambda error: (tuple(error.absolute_path), error.validator or "", error.message),
    )
    if errors:
        raise CalibrationEvidenceError(f"invalid {label}: {errors[0].message}")


def _render_architecture_prompt(
    planning: Mapping[str, Any],
    constraints: Mapping[str, Any],
    repair_context: Any,
    schema: Mapping[str, Any],
    example: Any,
    template_bytes: bytes | None = None,
):
    definition = get_role("architecture_planner")
    kwargs = {
        "inputs": {"planning_index": planning, "delivery_constraints": constraints, "repair_context": repair_context},
        "output_schema": dict(schema),
        "output_example": example,
    }
    if template_bytes is None:
        return PromptRenderer.render(definition, **kwargs)
    return PromptRenderer.render_template_bytes(definition, template=template_bytes, **kwargs)


class ArchitecturePlannerContractBinding:
    """Bind the one production contract to an existing AgentInvoker."""

    def __init__(self, invoker: AgentInvoker) -> None:
        self.invoker = invoker
        self.schema, self.example = architecture_draft_contract()

    def invoke(
        self,
        *,
        planning_index: Mapping[str, Any],
        delivery_constraints: Mapping[str, Any],
        repair_context: Any,
        run_id: str,
        task_id: str,
        attempt: int,
        use_cache: bool = False,
        template_bytes: bytes | None = None,
    ) -> AgentResult:
        return self.invoker.invoke(
            role="architecture_planner",
            inputs={"planning_index": planning_index, "delivery_constraints": delivery_constraints, "repair_context": repair_context},
            output_schema=self.schema,
            output_example=self.example,
            run_id=run_id,
            stage="S4",
            task_id=task_id,
            attempt=attempt,
            use_cache=use_cache,
            template_bytes=template_bytes,
        )


def bind_architecture_planner_contract(invoker: AgentInvoker) -> ArchitecturePlannerContractBinding:
    return ArchitecturePlannerContractBinding(invoker)


def _trace_ref(root: Path, trace: Mapping[str, Any] | None, field: str = "output_path") -> dict[str, str] | None:
    if not trace:
        return None
    path = trace.get(field)
    if not isinstance(path, str):
        return None
    full = root / path
    if not full.exists():
        return None
    return _ref(path, full.read_bytes())


def _trace_refs(root: Path, trace: Mapping[str, Any] | None, list_field: str, fallback_field: str) -> list[dict[str, str]]:
    if not trace:
        return []
    paths = trace.get(list_field)
    if not isinstance(paths, list):
        paths = []
    refs: list[dict[str, str]] = []
    for path in paths:
        if isinstance(path, str) and (root / path).is_file():
            refs.append(_ref(path, (root / path).read_bytes()))
    fallback = _trace_ref(root, trace, fallback_field)
    if fallback is not None and fallback not in refs:
        refs.append(fallback)
    return refs


def _call_metrics(root: Path, trace: Mapping[str, Any] | None, response_refs: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild provider-call accounting from immutable output evidence."""

    metrics: list[dict[str, Any]] = []
    for ref in response_refs:
        try:
            value = json.loads(_verify_ref(root, ref, "provider output").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, CalibrationEvidenceError):
            continue
        if not isinstance(value, Mapping) or "tokens_in" not in value:
            continue
        metadata = value.get("provider_metadata") if isinstance(value.get("provider_metadata"), Mapping) else {}
        finish_reason = metadata.get("finish_reason")
        metrics.append({
            "provider": str((trace or {}).get("provider") or str((trace or {}).get("model", "unknown")).split("/", 1)[0]),
            "requested_model": str((trace or {}).get("requested_model") or str((trace or {}).get("model", "unknown")).split("/", 1)[-1]),
            "model": value.get("model"),
            "parameter_support": value.get("parameter_support", {}),
            "tokens_in": int(value.get("tokens_in", 0)),
            "tokens_out": int(value.get("tokens_out", 0)),
            "cost_usd": float(value.get("cost_usd", 0)),
            "transport_attempts": int(value.get("transport_attempts", metadata.get("transport_attempts", 1))),
            "finish_reason": finish_reason,
            "truncated": finish_reason in {"length", "max_tokens"},
        })
    if not metrics:
        trace_value = trace or {}
        model = str(trace_value.get("model", "unknown"))
        metrics.append({
            "provider": str(trace_value.get("provider") or model.split("/", 1)[0]),
            "requested_model": str(trace_value.get("requested_model") or model.split("/", 1)[-1]),
            "model": model.split("/", 1)[-1],
            "parameter_support": trace_value.get("parameter_support", {}),
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
            "transport_attempts": max(1, int(trace_value.get("transport_attempts", 1))),
            "finish_reason": None, "truncated": False,
        })
    return metrics


def _summarize_call_metrics(metrics: list[Mapping[str, Any]], trace: Mapping[str, Any] | None) -> dict[str, Any]:
    finish = next((item.get("finish_reason") for item in reversed(metrics) if item.get("finish_reason") is not None), None)
    support: dict[str, Any] = {}
    for item in metrics:
        for key, value in item.get("parameter_support", {}).items():
            support[key] = value
    return {
        "call_count": sum(int(item.get("transport_attempts", 1)) for item in metrics),
        "tokens_in": sum(int(item.get("tokens_in", 0)) for item in metrics),
        "tokens_out": sum(int(item.get("tokens_out", 0)) for item in metrics),
        "cost_usd": sum(float(item.get("cost_usd", 0)) for item in metrics),
        "latency_ms": int((trace or {}).get("latency_ms", 0)),
        "finish_reason": finish,
        "truncated": any(bool(item.get("truncated")) for item in metrics),
        "model": str(metrics[-1].get("model") or (trace or {}).get("model", "unknown")),
        "parameter_support": support,
        "call_metrics": [dict(item) for item in metrics],
    }


def _persist_trace_snapshot(store: RunStore, trial_id: str, depth: int, trace: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not trace:
        return None
    data = canonical_json_bytes(dict(trace))
    path = f"trace/trials/{trial_id}_p{depth}_{_sha(data)[:16]}.json"
    store.publish_immutable_bytes(path, data)
    return _ref(path, data)


def _failure_attempt_record(
    store: RunStore,
    trial_id: str,
    depth: int,
    trace: Mapping[str, Any] | None,
    responses: list[LLMResponse],
    *,
    call_count: int,
    format_repaired: bool,
    infrastructure_invalid: bool,
) -> dict[str, Any]:
    request_refs = _trace_refs(store.root, trace, "provider_prompt_paths", "prompt_path")
    response_refs = _trace_refs(store.root, trace, "provider_output_paths", "output_path")
    last = responses[-1] if responses else None
    metadata = (trace or {}).get("provider_metadata", {})
    metrics = _call_metrics(store.root, trace, response_refs)
    summary = _summarize_call_metrics(metrics, trace)
    return {
        "depth": depth,
        "schema_valid": False,
        "infrastructure_invalid": infrastructure_invalid,
        "format_repaired": format_repaired,
        "semantic_verdict": "not-evaluable",
        "candidate_ref": None,
        "validation_ref": None,
        "request_ref": request_refs[0] if request_refs else None,
        "response_ref": response_refs[-1] if response_refs else None,
        "request_refs": request_refs,
        "response_refs": response_refs,
        "trace_ref": _persist_trace_snapshot(store, trial_id, depth, trace),
        **summary,
        "prompt_sha256": (trace or {}).get("prompt_template_sha256"),
        "effective_prompt_sha256": (trace or {}).get("effective_prompt_sha256"),
        "gate_results": {},
    }


class ArchitectureCalibrationDriver:
    """Run three isolated logical-slot workers and atomically commit trial evidence."""

    def __init__(
        self,
        config: ResolvedConfig | None = None,
        *,
        runs_root: str | Path = "runs",
        provider_factory: Any | None = None,
        providers: Any | None = None,
        fault_hook: Callable[[str], None] | None = None,
        prompt_bytes: bytes | None = None,
        prompt_source_guard: Callable[[], None] | None = None,
        publish_reports: bool = True,
        lineage_statistics: Mapping[str, Any] | None = None,
        lineage_component_bytes: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = config or load_config()
        self.runs_root = Path(runs_root).resolve()
        self.provider_factory = provider_factory if provider_factory is not None else providers
        self.fault_hook = fault_hook
        if prompt_bytes is not None and not isinstance(prompt_bytes, bytes):
            raise CalibrationDeclarationError("prompt snapshot must be raw bytes")
        self.prompt_bytes = prompt_bytes
        self.prompt_source_guard = prompt_source_guard
        self.publish_reports = publish_reports
        self.lineage_statistics = dict(lineage_statistics or {})
        self.lineage_component_bytes = dict(lineage_component_bytes or {})
        self._adapter_lock = threading.Lock()
        self._adapter_owners: dict[int, tuple[str, str]] = {}
        self._adapter_objects: dict[int, Any] = {}
        self._session_owners: dict[int, tuple[str, str, str]] = {}
        self._session_objects: dict[int, Any] = {}

    def _register_adapters(self, model_id: str, adapters: Mapping[str, Any]) -> None:
        """Reject one adapter object being used by more than one model worker."""

        if not isinstance(adapters, Mapping):
            raise CalibrationDeclarationError("provider factory must return an adapter mapping")
        local_ids: set[int] = set()
        with self._adapter_lock:
            for provider_name, adapter in adapters.items():
                adapter_id = id(adapter)
                if adapter_id in local_ids:
                    raise CalibrationDeclarationError(f"provider factory returned one adapter more than once for {model_id}")
                local_ids.add(adapter_id)
                owner = self._adapter_owners.get(adapter_id)
                if owner is not None and owner != (model_id, str(provider_name)):
                    raise CalibrationDeclarationError(
                        f"provider adapter instance is shared between calibration workers: {owner[0]} and {model_id}"
                    )
                self._adapter_owners[adapter_id] = (model_id, str(provider_name))
                self._adapter_objects[adapter_id] = adapter
                for session_name in ("session", "client"):
                    session = getattr(adapter, session_name, None)
                    if session is None:
                        continue
                    session_id = id(session)
                    session_owner = self._session_owners.get(session_id)
                    if session_owner is not None and session_owner[:2] != (model_id, str(provider_name)):
                        raise CalibrationDeclarationError(
                            f"provider session is shared between calibration workers: {session_owner[0]} and {model_id}"
                        )
                    self._session_owners[session_id] = (model_id, str(provider_name), session_name)
                    self._session_objects[session_id] = session

    @staticmethod
    def _validate_batch_lineage_binding(batch: Mapping[str, Any], lineage: Mapping[str, Any]) -> None:
        models = lineage.get("slot_controls")
        model_id = batch.get("model_id")
        if not isinstance(models, Mapping) or not isinstance(model_id, str):
            raise CalibrationEvidenceError("lineage is missing controlled batch projections")
        model = models.get(model_id)
        trial_count = batch.get("trial_count")
        depth = batch.get("semantic_depth")
        trials = batch.get("trials")
        trial_start = batch.get("trial_start", 1)
        batch_kind = batch.get("batch_kind", "base")
        expected_trials = [f"trial_{index:03d}" for index in range(int(trial_start), int(trial_start) + int(trial_count))] if isinstance(trial_count, int) and trial_count > 0 and isinstance(trial_start, int) and trial_start > 0 else None
        checks = (
            (batch.get("lineage_id") == lineage.get("lineage_id"), "lineage id drift"),
            (expected_trials is not None and trials == expected_trials, "batch trial ids do not match its declared trial count"),
            (isinstance(depth, int) and not isinstance(depth, bool) and 0 <= depth <= 2, "batch semantic depth is invalid"),
            (isinstance(model, Mapping), f"model projection is missing from lineage: {model_id}"),
            (batch_kind in {"base", "extension"}, "batch kind is invalid"),
            (batch_kind != "extension" or (trial_count == 5 and trial_start == 6 and trials == [f"trial_{index:03d}" for index in range(6, 11)]), "extension batch controls are invalid"),
        )
        for valid, message in checks:
            if not valid:
                raise CalibrationEvidenceError(message)
        for field in ("provider", "temperature", "max_tokens", "context_window_tokens"):
            if batch.get(field) != model.get(field):
                raise CalibrationEvidenceError(f"batch {field} is not bound to lineage model projection")

    def _prepare(self, declaration: CalibrationBatchDeclaration) -> tuple[PreparedArchitectureInputs, dict[str, Any], dict[str, Any], dict[str, Any]]:
        verify_design_baseline()
        if declaration.prepared_inputs is not None:
            prepared = prepare_architecture_inputs(
                declaration.prepared_inputs.spec_bytes,
                declaration.prepared_inputs.target_bytes,
                declaration.prepared_inputs.test_bundle_bytes,
            )
        else:
            spec = declaration.spec or "gold_file/specIR.json"
            target = declaration.target_profile or self.config.assets.target_profile
            bundle = declaration.test_bundle or self.config.assets.test_bundle
            prepared = prepare_architecture_inputs(spec, target, bundle)
        constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
        manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
        planning = build_planning_index(prepared, manifest, constraints)
        return prepared, planning, manifest, constraints

    def _publish_lineage(
        self,
        prepared: PreparedArchitectureInputs,
        planning: Mapping[str, Any],
        manifest: Mapping[str, Any],
        constraints: Mapping[str, Any],
        targets: Mapping[str, CalibrationModelTarget],
        declaration: CalibrationBatchDeclaration,
    ) -> tuple[RunStore, dict[str, Any]]:
        lineage = build_lineage_manifest(
            prepared, planning, manifest, constraints, config=self.config, model_targets=targets,
            context_window_tokens={model_id: target.context_window_tokens for model_id, target in targets.items()},
            statistics=self.lineage_statistics,
            component_bytes=self.lineage_component_bytes,
        )
        lineage_root = self.runs_root / "_calibration" / "s4-architecture" / lineage["lineage_id"]
        store = RunStore(lineage_root)
        store.root.mkdir(parents=True, exist_ok=True)
        _publish_json(store, "lineage.json", lineage, schema_name="calibration-lineage.schema.json")
        store.publish_immutable_bytes("inputs/spec.json", prepared.spec_bytes)
        store.publish_immutable_bytes("inputs/target.json", prepared.target_bytes)
        store.publish_immutable_bytes("inputs/test_bundle.json", prepared.test_bundle_bytes)
        store.publish_immutable_json("planning_index.json", planning)
        store.publish_immutable_json("test_manifest_metadata.json", manifest)
        store.publish_immutable_json("delivery_constraints.json", constraints)
        convention, _convention_sha256 = canonical_layout_convention(prepared.target_profile)
        store.publish_immutable_json("layout_convention.json", convention)
        store.publish_immutable_bytes("schema/architecture-draft.schema.json", (Path(__file__).resolve().parents[1] / "schemas/architecture-draft.schema.json").read_bytes())
        store.publish_immutable_bytes("schema/architecture-draft.example.json", (Path(__file__).resolve().parents[1] / "schemas/examples/architecture-draft.example.json").read_bytes())
        components = _component_values(component_bytes=self.lineage_component_bytes)
        for name, data in components.items():
            reference = lineage["components"].get(name)
            if not isinstance(reference, Mapping) or reference.get("path") != _component_path(name):
                raise CalibrationDeclarationError(f"lineage component path is not controlled: {name}")
            if reference.get("sha256") != _sha(data):
                raise CalibrationDeclarationError(f"lineage component bytes changed before publication: {name}")
            store.publish_immutable_bytes(reference["path"], data)
        return store, lineage

    def _model_worker(
        self,
        lineage_store: RunStore,
        lineage: Mapping[str, Any],
        model_id: str,
        target: CalibrationModelTarget,
        declaration: CalibrationBatchDeclaration,
        planning: Mapping[str, Any],
        manifest: Mapping[str, Any],
        constraints: Mapping[str, Any],
    ) -> ArtifactRef:
        prompt_version = _validate_prompt_version(declaration.prompt_version)
        prompt_root = _confined_path(lineage_store.root, prompt_version)
        if declaration.root_relative_path is not None:
            root = _confined_path(lineage_store.root, declaration.root_relative_path, model_id)
        else:
            root = prompt_root / model_id if declaration.attempt == 1 else prompt_root / f"attempt_{declaration.attempt:03d}" / model_id
        root = _confined_path(lineage_store.root, *root.relative_to(lineage_store.root).parts)
        root.mkdir(parents=True, exist_ok=True)
        if self.prompt_source_guard is not None:
            self.prompt_source_guard()
        store = RunStore(root)
        derived = _derived_config(self.config, target)
        adapters = _provider_map(self.provider_factory, model_id, target, derived, store)
        self._register_adapters(model_id, adapters)
        telemetry = LLMTelemetry(store, secret_env_names={item.api_key_env for item in derived.providers.values() if item.api_key_env})
        client = LLMClient(derived, adapters, store=store, telemetry=telemetry)
        invoker = AgentInvoker(derived, client)
        binding = ArchitecturePlannerContractBinding(invoker)
        schema, example = architecture_draft_contract()
        template_bytes = self.prompt_bytes if self.prompt_bytes is not None else PromptRenderer._load_template(get_role("architecture_planner")).raw
        rendered = architecture_planner_context_preflight(
            planning, constraints, model_limits={model_id: target.context_window_tokens},
            requested_output_tokens=target.max_tokens, safety_margin_ratio=derived.planning.context_safety_margin_ratio,
            output_schema=schema, output_example=example, template_bytes=template_bytes,
        )
        rendered_prompt = _render_architecture_prompt(planning, constraints, None, schema, example, template_bytes)
        actual_prompt_hash = _sha(template_bytes)
        if declaration.prompt_sha256 is not None and declaration.prompt_sha256 != actual_prompt_hash:
            raise CalibrationDeclarationError("declared prompt_sha256 does not match the actual ArchitecturePlanner template bytes")
        template_ref = _ref("prompt/template.md", template_bytes)
        store.publish_immutable_bytes(template_ref["path"], template_bytes)
        prompt_hash = actual_prompt_hash
        batch = {
            "schema_version": "2.0", "status": "declared", "lineage_id": lineage["lineage_id"],
            "prompt_version": declaration.prompt_version, "prompt_sha256": prompt_hash, "model_id": model_id,
            "prompt_template_ref": template_ref,
            "provider": target.provider, "model": target.model, "trial_count": declaration.trial_count,
            "temperature": target.temperature, "max_tokens": target.max_tokens,
            "semantic_depth": declaration.semantic_depth, "context_window_tokens": target.context_window_tokens,
            "trials": list(declaration.trial_names()),
            "attempt": declaration.attempt,
            "batch_kind": declaration.batch_kind,
            "trial_start": declaration.trial_start,
        }
        if declaration.root_relative_path is not None:
            batch["root_path"] = str(root.relative_to(lineage_store.root))
        _publish_json(store, "batch.json", batch, schema_name="calibration-batch.schema.json")
        for trial_id in declaration.trial_names():
            final = store.root / "trials" / trial_id
            if final.exists():
                if (final / "validation.json").exists() and (final / "request_ref.json").exists() and (final / "response_ref.json").exists():
                    _read_trial(store, final, batch, lineage)
                    continue
                raise CalibrationEvidenceError(f"uncommitted or incomplete trial directory cannot be reused: {trial_id}")
            self._run_trial(store, lineage, model_id, target, declaration, binding, planning, manifest, constraints, trial_id, prompt_hash, template_bytes)
        report = recompute_calibration_report(store.root, config=self.config)
        if self.publish_reports and report["status"] == "complete":
            _publish_json(store, "calibration_report.json", report, schema_name="calibration-report.schema.json")
        return ArtifactRef(
            str(store.root.relative_to(lineage_store.root)),
            _sha(canonical_json_bytes(report)),
        )

    @staticmethod
    def _prompt_hash(config: ResolvedConfig, planning: Mapping[str, Any], constraints: Mapping[str, Any], schema: Mapping[str, Any], example: Any) -> str:
        return _sha(PromptRenderer._load_template(get_role("architecture_planner")).raw)

    def _run_trial(
        self,
        store: RunStore,
        lineage: Mapping[str, Any],
        model_id: str,
        target: CalibrationModelTarget,
        declaration: CalibrationBatchDeclaration,
        binding: ArchitecturePlannerContractBinding,
        planning: Mapping[str, Any],
        manifest: Mapping[str, Any],
        constraints: Mapping[str, Any],
        trial_id: str,
        prompt_hash: str,
        template_bytes: bytes,
    ) -> None:
        staging = store.root / "trials" / f".{trial_id}.{os.getpid()}.staging"
        staging.mkdir(parents=True, exist_ok=True)
        attempts: list[dict[str, Any]] = []
        candidate: dict[str, Any] | None = None
        first_passing: int | None = None
        terminal = "semantic-fail"
        previous_context: Any = None
        for depth in range(declaration.semantic_depth + 1):
            prior_trace_count = len(_trace_rows(store, trial_id))
            try:
                if self.prompt_source_guard is not None:
                    self.prompt_source_guard()
                architecture_planner_context_preflight(
                    planning, constraints,
                    model_limits={model_id: target.context_window_tokens},
                    requested_output_tokens=target.max_tokens,
                    safety_margin_ratio=self.config.planning.context_safety_margin_ratio,
                    output_schema=binding.schema,
                    output_example=binding.example,
                    repair_context=previous_context,
                    template_bytes=template_bytes,
                )
                result = binding.invoke(
                    planning_index=planning, delivery_constraints=constraints, repair_context=previous_context,
                    run_id=f"calibration:{lineage['lineage_id']}:{declaration.prompt_version}:{model_id}",
                    task_id=trial_id, attempt=depth + 1, use_cache=False,
                    template_bytes=template_bytes,
                )
                if self.prompt_source_guard is not None:
                    self.prompt_source_guard()
                response = result.response
                candidate = result.parsed if isinstance(result.parsed, dict) else None
                trace = _latest_trace(store, trial_id, after=prior_trace_count)
                schema_valid = candidate is not None
                candidate_ref = None
                if schema_valid:
                    candidate_ref = _ref(f"candidates/{trial_id}_p{depth}.json", serialize_architecture_draft(candidate))
                validation = validate_architecture(
                    candidate,
                    planning,
                    manifest,
                    constraints,
                    parent_refs={
                        "architecture_draft": candidate_ref,
                        "planning_index": lineage["artifacts"]["planning_index"],
                        "manifest_metadata": lineage["artifacts"]["manifest_metadata"],
                        "delivery_constraints": lineage["artifacts"]["delivery_constraints"],
                    },
                ) if schema_valid else None
                if validation is not None and validation["verdict"] == "pass":
                    first_passing = depth
                    terminal = "pass"
                else:
                    terminal = "semantic-fail" if schema_valid else "schema-fail"
                attempts.append(self._attempt_record(store, trial_id, depth, response, candidate, validation, trace, schema_valid, response.repair_attempts > 0))
                if terminal == "pass" or not schema_valid or depth >= declaration.semantic_depth:
                    break
                previous_context = {"previous_candidate": candidate, "validation_issues": validation["issues"]}
            except StructuredOutputError as exc:
                trace = _latest_trace(store, trial_id, after=prior_trace_count)
                responses = list(exc.responses)
                attempts.append(_failure_attempt_record(
                    store, trial_id, depth, trace, responses,
                    call_count=len(responses) or 1,
                    format_repaired=len(responses) > 1 or bool((trace or {}).get("repair_attempts")),
                    infrastructure_invalid=False,
                ))
                terminal = "schema-fail"
                break
            except (DecodingError, TransportError, ProviderError, CalibrationError) as exc:
                trace = _latest_trace(store, trial_id, after=prior_trace_count)
                responses = list(getattr(exc, "responses", []))
                completed = getattr(exc, "completed_response", None)
                if completed is not None and not responses:
                    responses = [completed]
                attempts.append(_failure_attempt_record(
                    store, trial_id, depth, trace, responses,
                    call_count=getattr(exc, "attempts", len(responses) or 1),
                    format_repaired=len(responses) > 1 or bool((trace or {}).get("repair_attempts")),
                    infrastructure_invalid=True,
                ))
                terminal = "infrastructure-invalid"
                break
        validation_value = {
            "schema_version": "2.0", "lineage_id": lineage["lineage_id"], "prompt_version": declaration.prompt_version,
            "model_id": model_id, "trial_id": trial_id, "semantic_depth_declared": declaration.semantic_depth,
            "attempts": attempts, "terminal": terminal, "first_passing_depth": first_passing,
        }
        request_value = {
            "schema_version": "2.0", "lineage_id": lineage["lineage_id"], "prompt_version": declaration.prompt_version,
            "model_id": model_id, "trial_id": trial_id,
            "attempts": [{"depth": item["depth"], "kind": "initial" if item["depth"] == 0 else "semantic_repair", "request": item.get("request_ref") or {"path": "trace/missing", "sha256": "0" * 64}, "request_evidence": item.get("request_refs", []), "prompt_sha256": prompt_hash, "effective_prompt_sha256": item.get("effective_prompt_sha256") or "0" * 64} for item in attempts],
        }
        response_value = {
            "schema_version": "2.0", "lineage_id": lineage["lineage_id"], "prompt_version": declaration.prompt_version,
            "model_id": model_id, "trial_id": trial_id,
            "attempts": [{"depth": item["depth"], "kind": "initial" if item["depth"] == 0 else "semantic_repair", "response": item.get("response_ref") or item.get("trace_ref") or {"path": "trace/missing", "sha256": "0" * 64}, "response_evidence": item.get("response_refs", []), "candidate": item.get("candidate_ref"), "trace": item.get("trace_ref") or {"path": "trace/missing", "sha256": "0" * 64}, "effective_prompt_sha256": item.get("effective_prompt_sha256") or "0" * 64} for item in attempts],
        }
        staging_store = RunStore(staging)
        _publish_json(staging_store, "request_ref.json", request_value, schema_name="trial-request-ref.schema.json")
        validation_data = canonical_json_bytes(validation_value)
        _publish_json(staging_store, "validation.json", validation_value, schema_name="trial-validation.schema.json")
        response_value["validation"] = _ref(f"trials/{trial_id}/validation.json", validation_data)
        # The response index is rewritten in the staging directory before the
        # directory rename; the final path is therefore already hash-bound.
        _publish_json(staging_store, "response_ref.json", response_value, schema_name="trial-response-ref.schema.json")
        hook = declaration.fault_hook or self.fault_hook
        if hook:
            hook("trial_before_rename")
        store.root.joinpath("trials").mkdir(parents=True, exist_ok=True)
        os.replace(staging, store.root / "trials" / trial_id)
        RunStore._directory_fsync(store.root / "trials")
        if hook:
            hook("trial_after_rename")

    @staticmethod
    def _attempt_record(store: RunStore, trial_id: str, depth: int, response: LLMResponse, candidate: Any, validation: Any, trace: Mapping[str, Any] | None, schema_valid: bool, format_repaired: bool) -> dict[str, Any]:
        candidate_ref = None
        validation_ref = None
        if candidate is not None:
            candidate_data = serialize_architecture_draft(candidate)
            candidate_path = f"candidates/{trial_id}_p{depth}.json"
            candidate_ref = _ref(candidate_path, candidate_data)
            # Candidate evidence belongs to the model root, not the staging directory.
            store.publish_immutable_bytes(candidate_path, candidate_data)
        if validation is not None:
            validation_path = f"validations/{trial_id}_p{depth}.json"
            validation_data = canonical_json_bytes(validation)
            validation_ref = _ref(validation_path, validation_data)
            store.publish_immutable_bytes(validation_path, validation_data)
        request_refs = _trace_refs(store.root, trace, "provider_prompt_paths", "prompt_path")
        response_refs = _trace_refs(store.root, trace, "provider_output_paths", "output_path")
        metrics = _call_metrics(store.root, trace, response_refs)
        summary = _summarize_call_metrics(metrics, trace)
        return {
            "depth": depth, "schema_valid": schema_valid, "format_repaired": format_repaired,
            "infrastructure_invalid": False,
            "semantic_verdict": "pass" if validation and validation["verdict"] == "pass" else ("fail" if validation else "not-evaluable"),
            "candidate_ref": candidate_ref, "validation_ref": validation_ref,
            "request_ref": request_refs[0] if request_refs else None, "response_ref": response_refs[-1] if response_refs else None,
            "request_refs": request_refs, "response_refs": response_refs, "trace_ref": _persist_trace_snapshot(store, trial_id, depth, trace),
            **summary,
            "prompt_sha256": (trace or {}).get("prompt_template_sha256"),
            "effective_prompt_sha256": (trace or {}).get("effective_prompt_sha256"),
            "gate_results": {gate["id"]: gate["verdict"] for gate in (validation or {}).get("gates", [])},
        }

    def run(self, declaration: CalibrationBatchDeclaration | Mapping[str, Any]) -> Mapping[str, ArtifactRef]:
        verify_design_baseline()
        if not isinstance(declaration, CalibrationBatchDeclaration):
            declaration = CalibrationBatchDeclaration(**dict(declaration))
        targets = declaration.targets(self.config)
        prepared, planning, manifest, constraints = self._prepare(declaration)
        lineage_store, lineage = self._publish_lineage(prepared, planning, manifest, constraints, targets, declaration)
        results: dict[str, ArtifactRef] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                model_id: executor.submit(self._model_worker, lineage_store, lineage, model_id, targets[model_id], declaration, planning, manifest, constraints)
                for model_id in MODEL_IDS
            }
            for model_id in MODEL_IDS:
                results[model_id] = futures[model_id].result()
        return results


def _load_model_root(model_root: str | Path, *, config: ResolvedConfig | None = None) -> tuple[RunStore, dict[str, Any], dict[str, Any]]:
    store = RunStore(model_root)
    try:
        batch = json.loads((store.root / "batch.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationEvidenceError(f"unable to load batch declaration: {exc}") from exc
    if not isinstance(batch, dict):
        raise CalibrationEvidenceError("batch declaration must be an object")
    _validate_schema_artifact(batch, "calibration-batch.schema.json", "batch declaration")
    lineage_path = next((parent / "lineage.json" for parent in [store.root, *store.root.parents] if (parent / "lineage.json").is_file()), None)
    if lineage_path is None:
        raise CalibrationEvidenceError("unable to locate lineage manifest")
    try:
        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationEvidenceError(f"unable to load lineage: {exc}") from exc
    _validate_schema_artifact(lineage, "calibration-lineage.schema.json", "lineage manifest")
    lineage_root = lineage_path.parent.resolve()
    if lineage_root.name != lineage.get("lineage_id"):
        raise CalibrationEvidenceError("lineage root directory is not bound to lineage id")
    projection = dict(lineage)
    recorded_lineage_id = projection.pop("lineage_id", None)
    projection.pop("models", None)
    if not isinstance(recorded_lineage_id, str) or _sha(canonical_json_bytes(projection)) != recorded_lineage_id:
        raise CalibrationEvidenceError("lineage id does not match the persisted lineage projection")
    statistics = dict(lineage.get("statistics", {}))
    recorded_statistics_hash = statistics.pop("sha256", None)
    if not isinstance(recorded_statistics_hash, str) or _sha(canonical_json_bytes(statistics)) != recorded_statistics_hash:
        raise CalibrationEvidenceError("lineage statistics hash does not match the persisted statistics")
    for provider_name, provider in lineage.get("providers", {}).items():
        provider_projection = dict(provider)
        recorded_provider_hash = provider_projection.pop("sha256", None)
        if not isinstance(recorded_provider_hash, str) or _sha(canonical_json_bytes(provider_projection)) != recorded_provider_hash:
            raise CalibrationEvidenceError(f"lineage provider configuration hash mismatch: {provider_name}")
    for group_name in ("inputs", "artifacts"):
        group = lineage.get(group_name, {})
        for name, ref in group.items():
            _verify_ref(lineage_root, ref, f"lineage/{group_name}/{name}")
    components = lineage.get("components", {})
    current_components = _default_components()
    if set(components) != set(current_components):
        raise CalibrationEvidenceError("lineage controlled component set is incomplete")
    for name, ref in components.items():
        if ref.get("path") != _component_path(name):
            raise CalibrationEvidenceError(f"lineage component path is not controlled: {name}")
        _verify_ref(lineage_root, ref, f"lineage/components/{name}")
        expected_component = current_components.get(name)
        if name == "statistics" and lineage.get("statistics", {}).get("metric_definition") == RECOVERY_METRIC_DEFINITION:
            expected_component = recovery_component_bytes()
        if expected_component is None or _sha(expected_component) != ref.get("sha256"):
            raise CalibrationEvidenceError(f"controlled component drift: {name}")
    schema_path = Path(__file__).resolve().parents[1] / "schemas/architecture-draft.schema.json"
    example_path = Path(__file__).resolve().parents[1] / "schemas/examples/architecture-draft.example.json"
    for key, current_path in (("schema", schema_path), ("example", example_path)):
        recorded = lineage["artifacts"][key]
        if _sha(current_path.read_bytes()) != recorded.get("sha256"):
            raise CalibrationEvidenceError(f"controlled {key} artifact drift")
    if store.root.name != batch.get("model_id"):
        raise CalibrationEvidenceError("model root is not bound to batch model id")
    prompt_version = _validate_prompt_version(batch.get("prompt_version"))
    attempt = batch.get("attempt")
    root_path = batch.get("root_path")
    if isinstance(root_path, str):
        expected_root = _confined_path(lineage_root, *Path(root_path).parts)
    elif batch.get("batch_kind", "base") == "extension":
        raise CalibrationEvidenceError("extension batch is missing its confined root path")
    else:
        expected_root = lineage_root / prompt_version / batch["model_id"] if attempt == 1 else lineage_root / prompt_version / f"attempt_{attempt:03d}" / batch["model_id"]
    if store.root != expected_root.resolve():
        raise CalibrationEvidenceError("model root directory is not bound to batch prompt_version/attempt/model_id")
    if not isinstance(batch.get("prompt_template_ref"), Mapping) or batch["prompt_template_ref"].get("path") != "prompt/template.md":
        raise CalibrationEvidenceError("batch prompt template reference is not bound to prompt/template.md")
    template_data = _verify_ref(store.root, batch["prompt_template_ref"], "batch/prompt_template_ref")
    if _sha(template_data) != batch.get("prompt_sha256"):
        raise CalibrationEvidenceError("batch prompt_sha256 does not match the persisted template bytes")
    if config is not None:
        for provider_name, recorded in lineage.get("providers", {}).items():
            if provider_name not in config.providers:
                raise CalibrationEvidenceError(f"current configuration is missing lineage provider: {provider_name}")
            current = config.providers[provider_name].model_dump(mode="json")
            expected = dict(recorded)
            expected.pop("sha256", None)
            if current != expected:
                raise CalibrationEvidenceError(f"current provider configuration drift: {provider_name}")
        for model_id, model in lineage.get("models", {}).items():
            key = f"{model['provider']}/{model['model']}"
            price = config.pricing.models.get(key)
            if price is None or price.model_dump(mode="json") != lineage["pricing"][model_id]:
                raise CalibrationEvidenceError(f"current pricing drift: {model_id}")
    ArchitectureCalibrationDriver._validate_batch_lineage_binding(batch, lineage)
    return store, batch, lineage


def _verify_ref(root: Path, value: Any, label: str) -> bytes:
    if not isinstance(value, Mapping) or not isinstance(value.get("path"), str) or not isinstance(value.get("sha256"), str):
        raise CalibrationEvidenceError(f"{label} is not an artifact reference")
    relative = value["path"]
    if not relative or "\x00" in relative or relative.startswith("/") or "\\" in relative or ".." in Path(relative).parts:
        raise CalibrationEvidenceError(f"{label} has an unsafe path: {relative}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
        data = path.read_bytes()
    except (ValueError, OSError) as exc:
        raise CalibrationEvidenceError(f"missing or confined {label}: {value.get('path')}") from exc
    if _sha(data) != value["sha256"]:
        raise CalibrationEvidenceError(f"hash mismatch for {label}: {value['path']}")
    return data


def _read_trial(store: RunStore, trial_dir: Path, batch: Mapping[str, Any], lineage: Mapping[str, Any]) -> dict[str, Any]:
    for filename in ("request_ref.json", "response_ref.json", "validation.json"):
        if not (trial_dir / filename).is_file():
            raise CalibrationEvidenceError(f"incomplete trial directory: {trial_dir.name}")
    try:
        validation = json.loads((trial_dir / "validation.json").read_text(encoding="utf-8"))
        request = json.loads((trial_dir / "request_ref.json").read_text(encoding="utf-8"))
        response = json.loads((trial_dir / "response_ref.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationEvidenceError(f"invalid trial evidence {trial_dir.name}: {exc}") from exc
    _validate_schema_artifact(validation, "trial-validation.schema.json", f"{trial_dir.name}/validation.json")
    _validate_schema_artifact(request, "trial-request-ref.schema.json", f"{trial_dir.name}/request_ref.json")
    _validate_schema_artifact(response, "trial-response-ref.schema.json", f"{trial_dir.name}/response_ref.json")
    lineage_path = next((parent / "lineage.json" for parent in [store.root, *store.root.parents] if (parent / "lineage.json").is_file()), None)
    if lineage_path is None:
        raise CalibrationEvidenceError(f"unable to locate lineage for {trial_dir.name}")
    lineage_root = lineage_path.parent.resolve()
    template_data = _verify_ref(store.root, batch["prompt_template_ref"], f"{trial_dir.name}/prompt_template")
    frozen: dict[str, Any] = {}
    for key in ("planning_index", "manifest_metadata", "delivery_constraints"):
        try:
            frozen[key] = json.loads(_verify_ref(lineage_root, lineage["artifacts"][key], f"lineage/artifacts/{key}").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CalibrationEvidenceError(f"invalid frozen parent artifact {key} in {trial_dir.name}") from exc
    model_id = batch.get("model_id")
    for value, label in ((validation, "validation"), (request, "request index"), (response, "response index")):
        if value.get("lineage_id") != lineage.get("lineage_id") or value.get("prompt_version") != batch.get("prompt_version") or value.get("model_id") != model_id or value.get("trial_id") != trial_dir.name:
            raise CalibrationEvidenceError(f"cross-bound parent in {trial_dir.name}/{label}")
    validation_attempts = validation.get("attempts", [])
    request_attempts = request.get("attempts", [])
    response_attempts = response.get("attempts", [])
    if len(validation_attempts) != len(request_attempts) or len(validation_attempts) != len(response_attempts):
        raise CalibrationEvidenceError(f"duplicate or missing attempt evidence in {trial_dir.name}")
    depths = [item.get("depth") for item in validation_attempts]
    if depths != list(range(len(depths))):
        raise CalibrationEvidenceError(f"duplicate or out-of-order attempts in {trial_dir.name}")
    if validation.get("semantic_depth_declared") != batch.get("semantic_depth") or any(depth > int(batch.get("semantic_depth", 0)) for depth in depths):
        raise CalibrationEvidenceError(f"semantic repair depth is not bound to the batch in {trial_dir.name}")
    validation_parent = response.get("validation")
    if not isinstance(validation_parent, Mapping) or validation_parent.get("path") != f"trials/{trial_dir.name}/validation.json":
        raise CalibrationEvidenceError(f"validation parent path mismatch in {trial_dir.name}")
    _verify_ref(store.root, validation_parent, f"{trial_dir.name}/validation")
    previous_candidate: Any = None
    previous_issues: Any = None
    for index, (validation_attempt, request_attempt, response_attempt) in enumerate(zip(validation_attempts, request_attempts, response_attempts)):
        if not (validation_attempt.get("depth") == request_attempt.get("depth") == response_attempt.get("depth")):
            raise CalibrationEvidenceError(f"attempt depth mismatch in {trial_dir.name} at {index}")
        expected_kind = "initial" if index == 0 else "semantic_repair"
        if request_attempt.get("kind") != expected_kind or response_attempt.get("kind") != expected_kind:
            raise CalibrationEvidenceError(f"attempt kind mismatch in {trial_dir.name} at {index}")
        if request_attempt.get("prompt_sha256") != batch.get("prompt_sha256"):
            raise CalibrationEvidenceError(f"prompt hash mismatch in {trial_dir.name} at {index}")
        refs = {
            "request": request_attempt.get("request"),
            "response": response_attempt.get("response"),
            "candidate": response_attempt.get("candidate"),
            "trace": response_attempt.get("trace"),
            "validation_request": validation_attempt.get("request_ref"),
            "validation_response": validation_attempt.get("response_ref"),
            "validation_candidate": validation_attempt.get("candidate_ref"),
            "validation_result": validation_attempt.get("validation_ref"),
            "validation_trace": validation_attempt.get("trace_ref"),
        }
        request_evidence = request_attempt.get("request_evidence", [])
        response_evidence = response_attempt.get("response_evidence", [])
        validation_request_evidence = validation_attempt.get("request_refs", [])
        validation_response_evidence = validation_attempt.get("response_refs", [])
        if not request_evidence or not response_evidence or request_evidence != validation_request_evidence or response_evidence != validation_response_evidence:
            raise CalibrationEvidenceError(f"complete request/response evidence is missing or mismatched in {trial_dir.name} at {index}")
        if (
            refs["request"] != refs["validation_request"]
            or refs["response"] != refs["validation_response"]
            or refs["candidate"] != refs["validation_candidate"]
            or refs["trace"] != refs["validation_trace"]
            or refs["request"] != request_evidence[0]
            or refs["response"] != response_evidence[-1]
        ):
            raise CalibrationEvidenceError(f"attempt reference mismatch in {trial_dir.name} at {index}")
        if len(request_evidence) != len(set(item["path"] for item in request_evidence)) or len(response_evidence) != len(set(item["path"] for item in response_evidence)):
            raise CalibrationEvidenceError(f"duplicate request/response evidence in {trial_dir.name} at {index}")
        if validation_attempt.get("trace_ref") is None:
            raise CalibrationEvidenceError(f"missing trace evidence in {trial_dir.name} at {index}")
        all_refs = {**refs, "request_evidence": request_evidence, "response_evidence": response_evidence}
        for key, value in all_refs.items():
            values = value if key.endswith("evidence") else [value]
            for ref in values:
                if ref is None:
                    continue
                data = _verify_ref(store.root, ref, f"{trial_dir.name}/{key}")
                path = ref.get("path", "") if isinstance(ref, Mapping) else ""
                if "candidate" in key and trial_dir.name not in path:
                    raise CalibrationEvidenceError(f"cross-trial candidate evidence in {trial_dir.name}")
                if key == "validation_result":
                    try:
                        recorded_validation = json.loads(data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise CalibrationEvidenceError(f"invalid validation evidence in {trial_dir.name}") from exc
                    _validate_schema_artifact(recorded_validation, "architecture-validation.schema.json", f"{trial_dir.name}/{key}")
                if "trace" in key:
                    try:
                        trace = json.loads(data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise CalibrationEvidenceError(f"invalid trace evidence in {trial_dir.name}") from exc
                    if trace.get("task_id") != trial_dir.name:
                        raise CalibrationEvidenceError(f"cross-trial trace evidence in {trial_dir.name}")
                    if trace.get("prompt_template_sha256") not in {None, request_attempt.get("prompt_sha256", batch.get("prompt_sha256"))}:
                        raise CalibrationEvidenceError(f"prompt template hash mismatch in {trial_dir.name}")
                    trace_requests = _trace_refs(store.root, trace, "provider_prompt_paths", "prompt_path")
                    trace_responses = _trace_refs(store.root, trace, "provider_output_paths", "output_path")
                    if trace_requests != request_evidence or trace_responses != response_evidence:
                        raise CalibrationEvidenceError(f"trace evidence index mismatch in {trial_dir.name}")
                    expected_run_id = f"calibration:{lineage['lineage_id']}:{batch['prompt_version']}:{model_id}"
                    if (
                        trace.get("run_id") != expected_run_id
                        or trace.get("stage") != "S4"
                        or trace.get("agent_role") != "architecture_planner"
                        or trace.get("task_id") != trial_dir.name
                        or trace.get("attempt") != index + 1
                        or trace.get("provider") != batch.get("provider")
                        or trace.get("requested_provider") != batch.get("provider")
                        or trace.get("requested_model") != batch.get("model")
                        or trace.get("params_requested") != {"temperature": batch.get("temperature"), "max_tokens": batch.get("max_tokens")}
                        or trace.get("cached") is not False
                        or trace.get("use_cache") is not False
                        or trace.get("prompt_template_sha256") != batch.get("prompt_sha256")
                        or trace.get("effective_prompt_sha256") != validation_attempt.get("effective_prompt_sha256")
                    ):
                        raise CalibrationEvidenceError(f"trace identity or request configuration mismatch in {trial_dir.name} at {index}")
                    rendered = _render_architecture_prompt(
                        frozen["planning_index"], frozen["delivery_constraints"],
                        None if index == 0 else {
                            "previous_candidate": previous_candidate,
                            "validation_issues": previous_issues,
                        }, architecture_draft_contract()[0], architecture_draft_contract()[1],
                        template_data,
                    )
                    if index > 0 and (previous_candidate is None or previous_issues is None):
                        raise CalibrationEvidenceError(f"semantic repair lacks its prior Schema-valid candidate in {trial_dir.name} at {index}")
                    expected_prompt = (AGENT_SYSTEM_INSTRUCTION + "\n" + rendered.user).encode("utf-8")
                    fallback_suffix = (
                        "\n\nReturn one JSON value that conforms to this JSON Schema. "
                        "Do not omit required fields. JSON Schema:\n"
                        + canonical_json_bytes(architecture_draft_contract()[0]).decode("utf-8")
                    ).encode("utf-8")
                    expected_provider_prompts = [{expected_prompt, expected_prompt + fallback_suffix}]
                    if len(trace.get("provider_prompt_paths", [])) > 1:
                        try:
                            first_output = json.loads(_verify_ref(store.root, response_evidence[0], f"{trial_dir.name}/format_output").decode("utf-8"))
                            invalid_text = str(first_output.get("text", ""))
                            invalid_value = extract_first_json_value(invalid_text)
                            repair_errors = structured_validation_errors(architecture_draft_contract()[0], invalid_value)
                        except StructuredOutputError as exc:
                            repair_errors = exc.errors
                        repair_request = LLMClient._repair_request(
                            LLMRequest(role="architecture_planner", system=AGENT_SYSTEM_INSTRUCTION, user=rendered.user, json_schema=architecture_draft_contract()[0], temperature=float(batch["temperature"]), max_tokens=int(batch["max_tokens"])),
                            LLMResponse(text=invalid_text, tokens_in=0, tokens_out=0, cost_usd=0, model=str(batch["model"]), parameter_support={}),
                            repair_errors,
                        )
                        repair_prompt = (AGENT_SYSTEM_INSTRUCTION + "\n" + repair_request.user).encode("utf-8")
                        expected_provider_prompts.append({repair_prompt, repair_prompt + fallback_suffix})
                    prompt_path = trace.get("prompt_path")
                    prompt_ref = next((item for item in request_evidence if item.get("path") == prompt_path), None)
                    prompt_data = _verify_ref(store.root, prompt_ref, f"{trial_dir.name}/prompt") if prompt_ref is not None else None
                    if prompt_data not in expected_provider_prompts[0]:
                        raise CalibrationEvidenceError(f"actual prompt bytes do not match the bound inputs in {trial_dir.name} at {index}")
                    if _sha(prompt_data) != trace.get("prompt_sha256"):
                        raise CalibrationEvidenceError(f"actual provider prompt hash mismatch in {trial_dir.name} at {index}")
                    if _sha(expected_prompt) != trace.get("effective_prompt_sha256"):
                        raise CalibrationEvidenceError(f"effective prompt hash mismatch in {trial_dir.name} at {index}")
                    for prompt_index, provider_prompt in enumerate(trace.get("provider_prompt_paths", [])):
                        provider_data = _verify_ref(store.root, {"path": provider_prompt, "sha256": next(item["sha256"] for item in request_evidence if item["path"] == provider_prompt)}, f"{trial_dir.name}/provider_prompt")
                        allowed_prompts = expected_provider_prompts[min(prompt_index, len(expected_provider_prompts) - 1)]
                        if provider_data not in allowed_prompts:
                            raise CalibrationEvidenceError(f"provider prompt does not contain the complete bound prompt in {trial_dir.name} at {index}")
                if "candidate" in key:
                    try:
                        candidate = json.loads(data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise CalibrationEvidenceError(f"invalid candidate evidence in {trial_dir.name}") from exc
                    _validate_schema_artifact(candidate, "architecture-draft.schema.json", f"{trial_dir.name}/{key}")
                    response_value = json.loads(_verify_ref(store.root, response_evidence[-1], f"{trial_dir.name}/final_response").decode("utf-8"))
                    provider_candidate = response_value.get("parsed")
                    if provider_candidate is None:
                        try:
                            provider_candidate = extract_first_json_value(str(response_value.get("text", "")))
                        except StructuredOutputError as exc:
                            raise CalibrationEvidenceError(f"final provider output has no candidate JSON in {trial_dir.name} at {index}") from exc
                    if provider_candidate != candidate:
                        raise CalibrationEvidenceError(f"candidate is not derived from the bound provider output in {trial_dir.name} at {index}")
        candidate_ref = validation_attempt.get("candidate_ref")
        validation_ref = validation_attempt.get("validation_ref")
        call_metrics = _call_metrics(store.root, trace, response_evidence)
        if any(item.get("provider") != batch.get("provider") or item.get("requested_model") != batch.get("model") for item in call_metrics):
            raise CalibrationEvidenceError(f"provider/model call identity is not bound to the batch in {trial_dir.name} at {index}")
        summary = _summarize_call_metrics(call_metrics, trace)
        for field in ("call_count", "tokens_in", "tokens_out", "cost_usd", "latency_ms", "finish_reason", "truncated", "model", "parameter_support", "call_metrics"):
            if validation_attempt.get(field) != summary[field]:
                raise CalibrationEvidenceError(f"attempt metrics are not rebuilt from provider evidence in {trial_dir.name} at {index}")
        if request_attempt.get("effective_prompt_sha256") != validation_attempt.get("effective_prompt_sha256") or response_attempt.get("effective_prompt_sha256") != validation_attempt.get("effective_prompt_sha256"):
            raise CalibrationEvidenceError(f"request/response prompt evidence is not bound to the trace in {trial_dir.name} at {index}")
        if bool(validation_attempt.get("infrastructure_invalid")) != bool(trace.get("error")):
            raise CalibrationEvidenceError(f"infrastructure classification is not supported by provider evidence in {trial_dir.name} at {index}")
        if candidate_ref is None:
            if validation_ref is not None or validation_attempt.get("schema_valid") or validation_attempt.get("semantic_verdict") != "not-evaluable" or validation_attempt.get("gate_results"):
                raise CalibrationEvidenceError(f"candidate-free attempt summary is inconsistent in {trial_dir.name} at {index}")
            continue
        if validation_ref is None:
            raise CalibrationEvidenceError(f"schema-valid attempt has no validation evidence in {trial_dir.name} at {index}")
        if candidate_ref.get("path") != f"candidates/{trial_dir.name}_p{validation_attempt['depth']}.json" or validation_ref.get("path") != f"validations/{trial_dir.name}_p{validation_attempt['depth']}.json":
            raise CalibrationEvidenceError(f"candidate or validation parent path mismatch in {trial_dir.name} at {index}")
        try:
            candidate_data = _verify_ref(store.root, candidate_ref, f"{trial_dir.name}/candidate_recompute")
            candidate = json.loads(candidate_data.decode("utf-8"))
            recorded_validation = json.loads(_verify_ref(store.root, validation_ref, f"{trial_dir.name}/validation_recompute").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CalibrationEvidenceError(f"invalid candidate or validation evidence in {trial_dir.name} at {index}") from exc
        _validate_schema_artifact(candidate, "architecture-draft.schema.json", f"{trial_dir.name}/candidate_recompute")
        expected_validation = validate_architecture(
            candidate,
            frozen["planning_index"],
            frozen["manifest_metadata"],
            frozen["delivery_constraints"],
            parent_refs={
                "architecture_draft": _ref(candidate_ref["path"], candidate_data),
                "planning_index": lineage["artifacts"]["planning_index"],
                "manifest_metadata": lineage["artifacts"]["manifest_metadata"],
                "delivery_constraints": lineage["artifacts"]["delivery_constraints"],
            },
        )
        if recorded_validation != expected_validation:
            raise CalibrationEvidenceError(f"validation summary is not a deterministic ARCH_VALIDATE result in {trial_dir.name} at {index}")
        expected_verdict = "pass" if expected_validation["verdict"] == "pass" else "fail"
        expected_gates = {gate["id"]: gate["verdict"] for gate in expected_validation["gates"]}
        if not validation_attempt.get("schema_valid") or validation_attempt.get("infrastructure_invalid") or validation_attempt.get("semantic_verdict") != expected_verdict or validation_attempt.get("gate_results") != expected_gates:
            raise CalibrationEvidenceError(f"attempt summary does not match ARCH_VALIDATE in {trial_dir.name} at {index}")
        previous_candidate = candidate
        previous_issues = expected_validation.get("issues", [])
    expected_first_passing = next((attempt["depth"] for attempt in validation_attempts if attempt.get("semantic_verdict") == "pass"), None)
    if validation.get("first_passing_depth") != expected_first_passing:
        raise CalibrationEvidenceError(f"first_passing_depth is not recomputed from candidates in {trial_dir.name}")
    if any(attempt.get("infrastructure_invalid") for attempt in validation_attempts):
        expected_terminal = "infrastructure-invalid"
    elif expected_first_passing is not None:
        expected_terminal = "pass"
    elif any(attempt.get("schema_valid") for attempt in validation_attempts):
        expected_terminal = "semantic-fail"
    else:
        expected_terminal = "schema-fail"
    if validation.get("terminal") != expected_terminal:
        raise CalibrationEvidenceError(f"terminal is not recomputed from trial evidence in {trial_dir.name}")
    if any(attempt.get("semantic_verdict") == "pass" for attempt in validation_attempts[:-1]):
        raise CalibrationEvidenceError(f"attempts continue after a passing candidate in {trial_dir.name}")
    return validation


def _metric(value: int, denominator: int) -> float:
    return value / denominator


def _recomputed_terminal(attempts: list[Mapping[str, Any]]) -> str:
    first_passing = next((attempt.get("depth") for attempt in attempts if attempt.get("semantic_verdict") == "pass"), None)
    if any(attempt.get("infrastructure_invalid") for attempt in attempts):
        return "infrastructure-invalid"
    if first_passing is not None:
        return "pass"
    if any(attempt.get("schema_valid") for attempt in attempts):
        return "semantic-fail"
    return "schema-fail"


def _recomputed_first_passing(attempts: list[Mapping[str, Any]]) -> int | None:
    return next((int(attempt["depth"]) for attempt in attempts if attempt.get("semantic_verdict") == "pass"), None)


def _stage_gate_values(attempts: list[Mapping[str, Any]], depth: int, gates: Mapping[str, Any]) -> dict[str, dict[str, bool | None]]:
    """Return the explicit cumulative candidate result at each declared stage."""

    result: dict[str, dict[str, bool | None]] = {gate: {f"p{stage}": None for stage in range(3)} for gate in gates}
    previous = {gate: False for gate in gates}
    for stage in range(depth + 1):
        current_attempt = next((attempt for attempt in attempts if attempt.get("depth") == stage), None)
        if current_attempt is not None:
            previous = {gate: current_attempt.get("gate_results", {}).get(gate) == "pass" for gate in gates}
        for gate in gates:
            result[gate][f"p{stage}"] = previous[gate]
    return result


def _gate_change(before: bool | None, after: bool | None) -> str | None:
    if before is None or after is None:
        return "not_declared"
    if before == after:
        return "unchanged"
    return "improved" if after else "regressed"


def _stage_gain(metrics: list[Mapping[str, Any]], gate: str, before: str, after: str, denominator: int) -> dict[str, Any] | None:
    values = [(item["gate_stages"][gate][before], item["gate_stages"][gate][after]) for item in metrics]
    if not values or any(left is None or right is None for left, right in values):
        return None
    improved = sum(not left and right for left, right in values)
    regressed = sum(left and not right for left, right in values)
    unchanged = sum(left == right for left, right in values)
    before_passed = sum(bool(left) for left, _right in values)
    after_passed = sum(bool(right) for _left, right in values)
    return {
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "denominator": denominator,
        "before_passed": before_passed,
        "after_passed": after_passed,
        "gain": _metric(after_passed - before_passed, denominator),
    }


def recompute_calibration_report(model_root: str | Path, *, config: ResolvedConfig | None = None) -> dict[str, Any]:
    """Reload only committed evidence and recompute the canonical report."""

    store, batch, lineage = _load_model_root(model_root, config=config)
    trial_root = store.root / "trials"
    trial_names = list(batch["trials"])
    if len(trial_names) != batch["trial_count"] or len(set(trial_names)) != len(trial_names):
        raise CalibrationEvidenceError("batch trial ids do not form the declared fixed sample")
    validations: list[dict[str, Any]] = []
    for name in trial_names:
        path = trial_root / name
        if not path.is_dir():
            raise CalibrationEvidenceError(f"batch is incomplete: {name}")
        validations.append(_read_trial(store, path, batch, lineage))
    recomputed_terminals = [_recomputed_terminal(item.get("attempts", [])) for item in validations]
    if any(terminal == "infrastructure-invalid" for terminal in recomputed_terminals):
        status = "infrastructure-invalid"
    else:
        status = "complete"
    denominator = len(validations)
    first_attempts = [item.get("attempts", [])[0] for item in validations]
    initial_schema = sum(bool(attempt.get("schema_valid") and not attempt.get("format_repaired")) for attempt in first_attempts)
    post_format_schema = sum(bool(attempt.get("schema_valid")) for attempt in first_attempts)
    raw_arch = sum(bool(item.get("attempts") and item["attempts"][0].get("schema_valid") and item["attempts"][0].get("semantic_verdict") == "pass" and not item["attempts"][0].get("format_repaired")) for item in validations)
    semantic_first = sum(bool(item.get("attempts") and item["attempts"][0].get("schema_valid") and item["attempts"][0].get("semantic_verdict") == "pass") for item in validations)
    passing_depths = [_recomputed_first_passing(item.get("attempts", [])) for item in validations]
    depth = int(batch.get("semantic_depth", 0))
    metrics: dict[str, Any] = {
        "schema_first_pass_rate": _metric(initial_schema, denominator),
        "schema_after_format_repair_rate": _metric(post_format_schema, denominator),
        "arch_raw_first_pass_rate": _metric(raw_arch, denominator),
        "arch_semantic_first_pass_rate": _metric(semantic_first, denominator),
        "p0": _metric(sum(value == 0 for value in passing_depths), denominator),
        "p1": _metric(sum(value is not None and value <= 1 for value in passing_depths), denominator) if depth >= 1 else None,
        "p2": _metric(sum(value is not None and value <= 2 for value in passing_depths), denominator) if depth >= 2 else None,
        "p1_reason": None if depth >= 1 else {"code": "SEMANTIC_DEPTH_NOT_DECLARED_P1", "message": "p1 is not declared for this batch"},
        "p2_reason": None if depth >= 2 else {"code": "SEMANTIC_DEPTH_NOT_DECLARED", "message": "p2 is not declared for this batch"},
    }
    gates: dict[str, dict[str, Any]] = {}
    for gate_index in range(1, 16):
        gate = f"arch_{gate_index:02d}"
        passed = sum(item.get("attempts", [])[-1].get("gate_results", {}).get(gate) == "pass" for item in validations)
        gates[gate] = {"passed": passed, "denominator": denominator, "rate": _metric(passed, denominator)}
    declared_depth = int(batch["semantic_depth"])
    stage_values = [_stage_gate_values(item.get("attempts", []), declared_depth, gates) for item in validations]
    gate_stages: dict[str, dict[str, dict[str, Any] | None]] = {}
    for gate in gates:
        gate_stages[gate] = {}
        for stage in range(3):
            stage_key = f"p{stage}"
            values = [item[gate][stage_key] for item in stage_values]
            if stage > declared_depth:
                gate_stages[gate][stage_key] = None
            else:
                passed = sum(bool(value) for value in values)
                gate_stages[gate][stage_key] = {"passed": passed, "denominator": denominator, "rate": _metric(passed, denominator)}
    cooccurrence: dict[str, dict[str, int]] = {left: {right: 0 for right in gates} for left in gates}
    for item in validations:
        final_gate_results = item.get("attempts", [])[-1].get("gate_results", {})
        failed = [gate for gate in gates if final_gate_results.get(gate) != "pass"]
        for left in failed:
            for right in failed:
                cooccurrence[left][right] += 1

    def empty_usage() -> dict[str, Any]:
        return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "latency_ms": 0, "finish_reasons": {}, "truncated": 0}

    def add_usage(total: dict[str, Any], attempt: Mapping[str, Any]) -> None:
        total["calls"] += int(attempt.get("call_count", 0))
        total["tokens_in"] += int(attempt.get("tokens_in", 0))
        total["tokens_out"] += int(attempt.get("tokens_out", 0))
        total["cost_usd"] += float(attempt.get("cost_usd", 0))
        total["latency_ms"] += int(attempt.get("latency_ms", 0))
        finish_reason = attempt.get("finish_reason")
        if finish_reason:
            total["finish_reasons"][finish_reason] = total["finish_reasons"].get(finish_reason, 0) + 1
        if attempt.get("truncated"):
            total["truncated"] += 1

    usage = empty_usage()
    format_usage = empty_usage()
    semantic_usage = empty_usage()
    format_repairs = 0
    semantic_repairs: dict[str, int] = {}
    model_versions: set[str] = set()
    model_string_counts: dict[str, int] = {}
    parameter_support: dict[str, set[str]] = {}
    trial_metrics: list[dict[str, Any]] = []
    for item_index, item in enumerate(validations):
        attempts = item.get("attempts", [])
        first = attempts[0]
        final_attempt = attempts[-1]
        final_gate_results = {gate: final_attempt.get("gate_results", {}).get(gate) == "pass" for gate in gates}
        initial_gate_results = {
            gate: first.get("gate_results", {}).get(gate) == "pass"
            for gate in gates
        }
        trial_gate_stages = stage_values[item_index]
        trial_gate_changes = {
            gate: {
                "p0_to_p1": _gate_change(trial_gate_stages[gate]["p0"], trial_gate_stages[gate]["p1"]),
                "p1_to_p2": _gate_change(trial_gate_stages[gate]["p1"], trial_gate_stages[gate]["p2"]),
            }
            for gate in gates
        }
        trial_usage = empty_usage()
        trial_format_usage = empty_usage()
        trial_semantic_usage = empty_usage()
        for attempt in attempts:
            add_usage(usage, attempt)
            add_usage(trial_usage, attempt)
            if attempt.get("format_repaired"):
                format_repairs += 1
                add_usage(format_usage, attempt)
                add_usage(trial_format_usage, attempt)
            if attempt.get("depth", 0) > 0:
                repair_key = f"p{attempt['depth']}"
                semantic_repairs[repair_key] = semantic_repairs.get(repair_key, 0) + 1
                add_usage(semantic_usage, attempt)
                add_usage(trial_semantic_usage, attempt)
            if attempt.get("model"):
                model_versions.add(attempt["model"])
            for key, value in attempt.get("parameter_support", {}).items():
                parameter_support.setdefault(key, set()).add(str(value))
            for call in attempt.get("call_metrics", []):
                if call.get("model"):
                    observed_model = str(call["model"])
                    model_versions.add(observed_model)
                    model_string_counts[observed_model] = model_string_counts.get(observed_model, 0) + 1
                elif call.get("requested_model"):
                    requested_model = str(call["requested_model"])
                    model_string_counts[requested_model] = model_string_counts.get(requested_model, 0) + 1
                for key, value in call.get("parameter_support", {}).items():
                    parameter_support.setdefault(key, set()).add(str(value))
        configured_model = str(batch.get("model"))
        model_string_counts.setdefault(configured_model, 0)
        passing_depth = _recomputed_first_passing(attempts)
        trial_metrics.append({
            "trial_id": item["trial_id"],
            "terminal": recomputed_terminals[item_index],
            "first_passing_depth": passing_depth,
            "schema_first_pass": bool(first.get("schema_valid") and not first.get("format_repaired")),
            "schema_after_format_repair": bool(first.get("schema_valid")),
            "semantic_first_pass": bool(first.get("schema_valid") and first.get("semantic_verdict") == "pass"),
            "p0": passing_depth == 0,
            "p1": (passing_depth is not None and passing_depth <= 1) if depth >= 1 else None,
            "p2": (passing_depth is not None and passing_depth <= 2) if depth >= 2 else None,
            "gates": {gate: {"initial_passed": initial_gate_results[gate], "final_passed": final_gate_results[gate]} for gate in gates},
            "gate_stages": trial_gate_stages,
            "gate_changes": trial_gate_changes,
            "usage": trial_usage,
            "repairs": {"format": sum(attempt.get("format_repaired") is True for attempt in attempts), "format_usage": trial_format_usage, "semantic": {"p1": sum(attempt.get("depth") == 1 for attempt in attempts), "p2": sum(attempt.get("depth") == 2 for attempt in attempts)}, "semantic_usage": trial_semantic_usage},
        })
    repair_gain = {}
    for gate in gates:
        initial_passed = sum(metric["gates"][gate]["initial_passed"] for metric in trial_metrics)
        final_passed = sum(metric["gates"][gate]["final_passed"] for metric in trial_metrics)
        initial_rate = _metric(initial_passed, denominator)
        final_rate = _metric(final_passed, denominator)
        repair_gain[gate] = {"initial_passed": initial_passed, "final_passed": final_passed, "denominator": denominator, "initial_rate": initial_rate, "final_rate": final_rate, "gain": final_rate - initial_rate}
    stage_gain = {
        gate: {
            "p0_to_p1": _stage_gain(trial_metrics, gate, "p0", "p1", denominator) if depth >= 1 else None,
            "p1_to_p2": _stage_gain(trial_metrics, gate, "p1", "p2", denominator) if depth >= 2 else None,
        }
        for gate in gates
    }
    report = {
        "schema_version": "2.0", "lineage_id": lineage["lineage_id"], "prompt_version": batch["prompt_version"],
        "prompt_sha256": batch["prompt_sha256"], "model_id": batch["model_id"], "trial_count": denominator, "status": status,
        "metrics": metrics, "gates": gates, "gate_stages": gate_stages, "failure_cooccurrence": cooccurrence,
        "repairs": {"format": format_repairs, "format_usage": format_usage, "semantic": semantic_repairs, "semantic_usage": semantic_usage, "gain": repair_gain, "stage_gain": stage_gain}, "usage": usage,
        "model_identity": {
            "provider": batch.get("provider"),
            "model": batch.get("model"),
            "versions": sorted(model_versions),
            "parameter_support": {key: sorted(values) for key, values in sorted(parameter_support.items())},
            "model_strings": sorted(model_string_counts),
            "model_string_shares": model_string_counts,
        },
        "trials": trial_names, "trial_metrics": trial_metrics,
    }
    return report


__all__ = [
    "ArchitectureCalibrationDriver", "ArchitecturePlannerContractBinding", "CalibrationBatchDeclaration", "CalibrationDeclarationError", "CalibrationError", "CalibrationEvidenceError", "CalibrationModelTarget", "DESIGN_BASELINE", "bind_architecture_planner_contract", "build_lineage_manifest", "recompute_calibration_report", "verify_design_baseline",
]
