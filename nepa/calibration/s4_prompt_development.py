"""Bounded M1-4a2 ArchitecturePlanner prompt development.

This module is deliberately a coordinator over the M1-4a1 driver.  It does
not introduce another parser, validator, provider call shape, or production
run path.  Development records are append-only and are useful only when all
referenced underlying evidence can be reloaded and recomputed.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from ..agents.base import PromptRenderer
from ..agents.roles import get_role
from ..config import ConfigError, ResolvedConfig, config_snapshot_sha256, load_config, public_config_snapshot
from ..run_store import ArtifactRef, RunStore, RunStoreError
from ..schemas import architecture_draft_contract, load_schema
from ..speclib.lint import canonical_json_bytes
from . import s4_architecture as _architecture
from .s4_architecture import (
    FIXED_API_KEY_ENVS,
    MODEL_IDS,
    METRIC_DEFINITION,
    RECOVERY_METRIC_DEFINITION,
    REPAIR_IMPACT_POLICY,
    REPAIR_IMPACT_POLICY_VERSION,
    ArchitectureCalibrationDriver,
    CalibrationBatchDeclaration,
    CalibrationDeclarationError,
    CalibrationEvidenceError,
    CalibrationError,
    _default_components,
    _render_architecture_prompt,
    _ref as calibration_ref,
    _sha,
    _verify_ref,
    assess_repair_locality,
    recompute_calibration_report,
    recovery_component_bytes,
)


class PromptDevelopmentError(RuntimeError):
    """Base error for the bounded development workflow."""


class PromptDevelopmentConfigError(PromptDevelopmentError):
    """Explicit calibration configuration failed preflight."""


class PromptDevelopmentEvidenceError(PromptDevelopmentError):
    """Committed development evidence is missing, drifting, or cross-bound."""


class PromptSelectionTie(PromptDevelopmentError):
    """The authoritative fallback tuple is exactly tied."""


_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_VERSION_RE = re.compile(r"^v[0-2]$")
_GATES = tuple(f"arch_{index:02d}" for index in range(1, 16))
_METRIC_NAMES = ("schema_after_format_repair_rate", "p1", "arch_semantic_first_pass_rate")
CONFIG_ENV = "NEPA_M1_4A2_CONFIG"
CONTEXT_LIMITS_ENV = "NEPA_M1_4A2_CONTEXT_LIMITS"
_SLOT_RETRY_EXCEPTION_PATH = "v2/extensions/n010/slot-retry-001/exception.json"
_SCHEMA_NAMES = {
    "protocol": "calibration-development-protocol.schema.json",
    "version": "calibration-prompt-version.schema.json",
    "snapshot": "calibration-prompt-snapshot.schema.json",
    "revision": "calibration-prompt-revision.schema.json",
    "attempt_declaration": "calibration-attempt-declaration.schema.json",
    "attempt_outcome": "calibration-attempt-outcome.schema.json",
    "extension": "calibration-development-extension.schema.json",
    "assessment": "calibration-development-assessment.schema.json",
    "outcome": "calibration-development-outcome.schema.json",
    "selection": "calibration-development-selection.schema.json",
    "handoff": "calibration-development-handoff.schema.json",
    "report": "calibration-report.schema.json",
}


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("/") or "\\" in value or not _SAFE_RELATIVE.fullmatch(value):
        raise PromptDevelopmentEvidenceError(f"unsafe development artifact path: {value!r}")
    return value


def _root_ref(root: Path, relative: str) -> dict[str, str]:
    relative = _safe_relative(relative)
    path = (root / relative).resolve(strict=False)
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PromptDevelopmentEvidenceError("development artifact escapes lineage root") from exc
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PromptDevelopmentEvidenceError(f"missing development artifact: {relative}") from exc
    return {"path": relative, "sha256": _sha(data)}


def _slot_retry_exception_authorized(root: Path) -> bool:
    path = root / _SLOT_RETRY_EXCEPTION_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("schema_version") == "2.0"
        and value.get("lineage_id") == root.name
        and value.get("version") == "v2"
        and value.get("extension_attempt") == 1
        and value.get("status") == "admitted"
        and value.get("exception") == "single_slot_retry"
        and value.get("authorization") == "explicit_user_authorization"
        and value.get("model_id") == "claude"
        and value.get("trial_id") == "trial_010"
    )


def _recompute_lineage_report(root: Path, model_root: str | Path, *, config: ResolvedConfig | None = None) -> dict[str, Any]:
    if not _slot_retry_exception_authorized(root):
        return recompute_calibration_report(model_root, config=config)
    recorded_components = {
        name: _verify_root_ref(root, ref, f"lineage/components/{name}")
        for name, ref in _load_json(root, "lineage.json").get("components", {}).items()
    }
    original_components = _architecture._default_components
    _architecture._default_components = lambda: dict(recorded_components)
    try:
        return recompute_calibration_report(model_root, config=config)
    finally:
        _architecture._default_components = original_components


def _verify_root_ref(root: Path, value: Any, label: str) -> bytes:
    try:
        return _verify_ref(root, value, label)
    except CalibrationEvidenceError as exc:
        raise PromptDevelopmentEvidenceError(str(exc)) from exc


def _publish_json(root: Path, relative: str, value: Any, schema_key: str) -> dict[str, str]:
    relative = _safe_relative(relative)
    store = RunStore(root)
    try:
        ref = store.publish_immutable_json(relative, value, schema_name=_SCHEMA_NAMES[schema_key])
    except RunStoreError as exc:
        raise PromptDevelopmentEvidenceError(str(exc)) from exc
    return ref.as_dict()


def _publish_bytes(root: Path, relative: str, data: bytes) -> dict[str, str]:
    relative = _safe_relative(relative)
    try:
        ref = RunStore(root).publish_immutable_bytes(relative, data)
    except RunStoreError as exc:
        raise PromptDevelopmentEvidenceError(str(exc)) from exc
    return ref.as_dict()


def _load_json(root: Path, relative: str, schema_key: str | None = None) -> dict[str, Any]:
    relative = _safe_relative(relative)
    try:
        value = json.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentEvidenceError(f"unable to load committed record {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise PromptDevelopmentEvidenceError(f"committed record is not an object: {relative}")
    if schema_key is not None:
        errors = sorted(Draft202012Validator(load_schema(_SCHEMA_NAMES[schema_key])).iter_errors(value), key=lambda item: tuple(item.absolute_path))
        if errors:
            raise PromptDevelopmentEvidenceError(f"invalid {relative}: {errors[0].message}")
    return value


def _read_json_ref(root: Path, ref: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        value = json.loads(_verify_root_ref(root, ref, label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentEvidenceError(f"invalid JSON evidence {label}") from exc
    if not isinstance(value, dict):
        raise PromptDevelopmentEvidenceError(f"JSON evidence is not an object: {label}")
    return value


def _source_prompt_bytes(path: Path | None = None) -> bytes:
    if path is not None:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise PromptDevelopmentEvidenceError(f"unable to read ArchitecturePlanner prompt source: {exc}") from exc
    return PromptRenderer._load_template(get_role("architecture_planner")).raw


def _prompt_path_from_record(record: Mapping[str, Any]) -> str:
    ref = record.get("prompt_ref")
    if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
        raise PromptDevelopmentEvidenceError("prompt record has no valid prompt reference")
    return ref["path"]


def scan_prompt_neutrality(prompt: bytes | str, *, forbidden_tokens: Mapping[str, str] | None = None) -> None:
    """Reject protocol/model/provider facts in raw shared prompt bytes."""

    raw = prompt if isinstance(prompt, bytes) else prompt.encode("utf-8")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptDevelopmentError("ArchitecturePlanner prompt snapshot is not UTF-8") from exc
    tokens = {
        "mqtt", "connack", "mosquitto", "subscribe", "subscription", "topic_filter", "paho",
        "REQ-CONNECT-001", "REQ-SUBSCRIBE-001", "1883",
    }
    if forbidden_tokens:
        tokens.update(str(value) for value in forbidden_tokens.values() if str(value).strip())
    lowered = text.casefold()
    for token in sorted(tokens, key=lambda item: (-len(item), item)):
        candidate = token.casefold()
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])", lowered):
            raise PromptDevelopmentError(f"prompt neutrality violation: {token}")


def _input_neutrality_tokens(planning: Mapping[str, Any], constraints: Mapping[str, Any], config: ResolvedConfig) -> dict[str, str]:
    values: dict[str, str] = {}
    for model_id, target in config.calibration_models.items():
        values[f"model:{model_id}"] = target.model
        values[f"provider:{model_id}"] = target.provider
    for provider_name in config.providers:
        values[f"provider-name:{provider_name}"] = provider_name
    for collection in (planning, constraints):
        encoded = json.dumps(collection, ensure_ascii=False, sort_keys=True)
        for match in re.findall(r"REQ[-A-Za-z0-9_]+|/[A-Za-z0-9_./-]+|\b\d{2,5}\b", encoded):
            values[f"input:{match}"] = match
    return values


@dataclass(frozen=True)
class CalibrationPreflight:
    config_path: Path
    context_limits_path: Path
    config: ResolvedConfig
    context_limits: dict[str, int]
    config_snapshot: dict[str, Any]
    config_sha256: str
    context_bytes: bytes

    @property
    def model_projection(self) -> dict[str, dict[str, Any]]:
        return {
            model_id: {
                "provider": self.config.calibration_models[model_id].provider,
                "model": self.config.calibration_models[model_id].model,
                "temperature": self.config.calibration_models[model_id].temperature,
                "max_tokens": self.config.calibration_models[model_id].max_tokens,
                "context_window_tokens": self.context_limits[model_id],
            }
            for model_id in MODEL_IDS
        }


def preflight_calibration_config(
    config_path: str | Path,
    context_limits_path: str | Path,
    *,
    require_environment: bool = True,
) -> CalibrationPreflight:
    """Resolve explicit non-secret inputs before any lineage/provider work."""

    if config_path is None or context_limits_path is None:
        raise PromptDevelopmentConfigError("calibration init requires explicit config and context-limits paths")
    config_file = Path(config_path).resolve()
    limits_file = Path(context_limits_path).resolve()
    try:
        config = load_config(config_file)
    except (ConfigError, OSError) as exc:
        raise PromptDevelopmentConfigError(f"unable to resolve explicit calibration configuration: {exc}") from exc
    try:
        raw_limits = json.loads(limits_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentConfigError(f"unable to read explicit context limits: {exc}") from exc
    if not isinstance(raw_limits, dict) or set(raw_limits) != set(MODEL_IDS):
        raise PromptDevelopmentConfigError("context limits must contain exactly qwen, claude, and deepseek")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in raw_limits.values()):
        raise PromptDevelopmentConfigError("context limits must be positive integers")
    if set(config.calibration_models) != set(MODEL_IDS):
        raise PromptDevelopmentConfigError("calibration_models must contain exactly qwen, claude, and deepseek")
    for model_id in MODEL_IDS:
        model = config.calibration_models[model_id]
        provider = config.providers.get(model.provider)
        if provider is None:
            raise PromptDevelopmentConfigError(f"missing provider for {model_id}")
        expected_env = FIXED_API_KEY_ENVS[model_id]
        if provider.api_key_env != expected_env:
            raise PromptDevelopmentConfigError(f"{model_id} must use api_key_env {expected_env}")
        if f"{model.provider}/{model.model}" not in config.pricing.models:
            raise PromptDevelopmentConfigError(f"missing pricing for {model_id}")
        if model.temperature < 0 or model.max_tokens != 65536:
            raise PromptDevelopmentConfigError(f"invalid request parameters for {model_id}")
    if require_environment:
        missing = [name for name in FIXED_API_KEY_ENVS.values() if not os.environ.get(name)]
        if missing:
            raise PromptDevelopmentConfigError("missing required environment variable: " + ", ".join(missing))
    snapshot = public_config_snapshot(config)
    context_bytes = canonical_json_bytes({model_id: int(raw_limits[model_id]) for model_id in MODEL_IDS})
    return CalibrationPreflight(
        config_path=config_file,
        context_limits_path=limits_file,
        config=config,
        context_limits={model_id: int(raw_limits[model_id]) for model_id in MODEL_IDS},
        config_snapshot=snapshot,
        config_sha256=config_snapshot_sha256(snapshot),
        context_bytes=context_bytes,
    )


def _verify_lineage(root: Path, lineage: Mapping[str, Any], preflight: CalibrationPreflight) -> None:
    if lineage.get("lineage_id") != root.name:
        raise PromptDevelopmentEvidenceError("lineage root directory is not bound to lineage id")
    projection = dict(lineage)
    lineage_id = projection.pop("lineage_id", None)
    projection.pop("models", None)
    if not isinstance(lineage_id, str) or _sha(canonical_json_bytes(projection)) != lineage_id:
        raise PromptDevelopmentEvidenceError("lineage id does not match its canonical projection")
    statistics = dict(lineage.get("statistics", {}))
    stats_hash = statistics.pop("sha256", None)
    if not isinstance(stats_hash, str) or _sha(canonical_json_bytes(statistics)) != stats_hash:
        raise PromptDevelopmentEvidenceError("lineage statistics hash mismatch")
    exception_authorized = _slot_retry_exception_authorized(root)
    if (
        statistics.get("metric_definition") != METRIC_DEFINITION
        or (
            statistics.get("implementation_sha256")
            != _sha((Path(__file__).resolve().parent / "s4_architecture.py").read_bytes())
            and not exception_authorized
        )
    ):
        raise PromptDevelopmentEvidenceError("lineage metric-definition implementation drift")
    if lineage.get("calibration", {}).get("api_key_env") != FIXED_API_KEY_ENVS:
        raise PromptDevelopmentEvidenceError("lineage fixed API-key mapping drift")
    expected_controls = {
        model_id: {
            "provider": preflight.config.calibration_models[model_id].provider,
            "temperature": preflight.config.calibration_models[model_id].temperature,
            "max_tokens": preflight.config.calibration_models[model_id].max_tokens,
            "context_window_tokens": preflight.context_limits[model_id],
        }
        for model_id in MODEL_IDS
    }
    if lineage.get("slot_controls") != expected_controls:
        raise PromptDevelopmentEvidenceError("resolved slot control projection drift")
    if set(lineage.get("models", {})) != set(MODEL_IDS):
        raise PromptDevelopmentEvidenceError("lineage model observations are incomplete")
    expected_provider_names = {preflight.config.calibration_models[model_id].provider for model_id in MODEL_IDS}
    if set(lineage.get("providers", {})) != expected_provider_names or set(lineage.get("pricing", {})) != set(MODEL_IDS):
        raise PromptDevelopmentEvidenceError("lineage provider or pricing projection drift")
    for model_id in MODEL_IDS:
        target = preflight.config.calibration_models[model_id]
        provider = preflight.config.providers.get(target.provider)
        recorded_value = lineage.get("providers", {}).get(target.provider)
        if provider is None or not isinstance(recorded_value, Mapping):
            raise PromptDevelopmentEvidenceError(f"provider configuration is missing from lineage: {model_id}")
        recorded_provider = dict(recorded_value)
        recorded_provider.pop("sha256", None)
        if recorded_provider != provider.model_dump(mode="json"):
            raise PromptDevelopmentEvidenceError(f"provider configuration drift: {model_id}")
        price = preflight.config.pricing.models.get(f"{target.provider}/{target.model}")
        if price is None or lineage["pricing"][model_id] != price.model_dump(mode="json"):
            raise PromptDevelopmentEvidenceError(f"pricing drift: {model_id}")
    for group in ("inputs", "artifacts", "components"):
        for name, ref in lineage.get(group, {}).items():
            _verify_root_ref(root, ref, f"lineage/{group}/{name}")
    current = _default_components()
    if set(current) != set(lineage.get("components", {})):
        raise PromptDevelopmentEvidenceError("controlled component set drift")
    for name, data in current.items():
        if lineage["components"][name].get("sha256") != _sha(data) and not (exception_authorized and name == "statistics"):
            raise PromptDevelopmentEvidenceError(f"controlled component drift: {name}")


def _protocol_root(runs_root: str | Path, lineage_id: str) -> Path:
    root = (Path(runs_root).resolve() / "_calibration" / "s4-architecture" / lineage_id).resolve()
    if root.name != lineage_id or not re.fullmatch(r"[0-9a-f]{64}", lineage_id):
        raise PromptDevelopmentEvidenceError("invalid lineage root")
    return root


def _load_protocol(root: Path, preflight: CalibrationPreflight | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = _load_json(root, "prompt-development/protocol.json", "protocol")
    lineage = _load_json(root, "lineage.json")
    if protocol.get("lineage_id") != lineage.get("lineage_id") or protocol.get("lineage_id") != root.name:
        raise PromptDevelopmentEvidenceError("protocol lineage binding drift")
    if protocol.get("model_ids") != list(MODEL_IDS) or protocol.get("versions") != ["v0", "v1", "v2"]:
        raise PromptDevelopmentEvidenceError("protocol model or version set drift")
    if protocol.get("semantic_depth") != 1 or protocol.get("base_trial_count") != 5 or protocol.get("extension_trial_count") != 5:
        raise PromptDevelopmentEvidenceError("protocol batch controls drift")
    if protocol.get("metric_definition") != METRIC_DEFINITION or protocol.get("api_key_env") != FIXED_API_KEY_ENVS:
        raise PromptDevelopmentEvidenceError("protocol controlled metric or key mapping drift")
    if protocol.get("screening") != {
        "p1_threshold": 0.80,
        "max_truncations": 0,
        "require_infrastructure_valid": True,
        "reference_p1_threshold": 0.90,
        "reference_relation": "strictly_below",
    }:
        raise PromptDevelopmentEvidenceError("protocol screening contract drift")
    if protocol.get("components") != lineage.get("components"):
        raise PromptDevelopmentEvidenceError("protocol component binding drift")
    if preflight is not None:
        _verify_lineage(root, lineage, preflight)
        config_value = json.loads(_verify_root_ref(root, protocol["config_ref"], "protocol/config").decode("utf-8"))
        context_value = _verify_root_ref(root, protocol["context_limits_ref"], "protocol/context").decode("utf-8")
        if config_value != preflight.config_snapshot or _sha(preflight.context_bytes) != protocol["context_limits_ref"]["sha256"] or context_value != preflight.context_bytes.decode("utf-8"):
            raise PromptDevelopmentEvidenceError("protocol configuration or context limit drift")
    return protocol, lineage


def _frozen_planning_context(root: Path, lineage: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        planning = json.loads(_verify_root_ref(root, lineage["artifacts"]["planning_index"], "lineage/planning_index").decode("utf-8"))
        constraints = json.loads(_verify_root_ref(root, lineage["artifacts"]["delivery_constraints"], "lineage/delivery_constraints").decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentEvidenceError("invalid frozen planning context") from exc
    if not isinstance(planning, dict) or not isinstance(constraints, dict):
        raise PromptDevelopmentEvidenceError("frozen planning context must be objects")
    return planning, constraints


def _version_dir(version: str) -> str:
    if _VERSION_RE.fullmatch(version) is None:
        raise PromptDevelopmentError("version must be v0, v1, or v2")
    return f"prompt-development/versions/{version}"


def _load_version(root: Path, version: str) -> dict[str, Any]:
    value = _load_json(root, f"{_version_dir(version)}/version.json", "version")
    if value.get("lineage_id") != root.name or value.get("version") != version:
        raise PromptDevelopmentEvidenceError("prompt version lineage or label binding drift")
    protocol_ref = value.get("protocol_ref")
    if not isinstance(protocol_ref, Mapping) or protocol_ref.get("path") != "prompt-development/protocol.json":
        raise PromptDevelopmentEvidenceError("prompt version is not bound to the development protocol")
    _verify_root_ref(root, protocol_ref, "prompt version protocol")
    prompt_ref = value.get("prompt_ref")
    expected_prompt_path = f"{_version_dir(version)}/prompt.md"
    if not isinstance(prompt_ref, Mapping) or prompt_ref.get("path") != expected_prompt_path:
        raise PromptDevelopmentEvidenceError("prompt version is not bound to its immutable snapshot bytes")
    prompt = _verify_root_ref(root, prompt_ref, "prompt version bytes")
    if value.get("prompt_sha256") != _sha(prompt) or value.get("source_prompt_sha256") != _sha(prompt):
        raise PromptDevelopmentEvidenceError("prompt version hash binding drift")
    return value


def _load_assessment(root: Path, version: str, count: int) -> dict[str, Any]:
    value = _load_json(root, f"{_version_dir(version)}/assessment-n{count:03d}.json", "assessment")
    if value.get("lineage_id") != root.name or value.get("version") != version or value.get("trial_count") != count:
        raise PromptDevelopmentEvidenceError("assessment lineage, version, or trial-count binding drift")
    return value


def _expected_report_path(version: str, attempt: int, count: int, model_id: str) -> str:
    if count == 5:
        prefix = f"{version}/"
        if attempt > 1:
            prefix += f"attempt_{attempt:03d}/"
        return f"{prefix}{model_id}/calibration_report.json"
    if count == 10:
        prefix = f"{version}/extensions/n010/"
        if attempt > 1:
            prefix += f"attempt_{attempt:03d}/"
        return f"{prefix}{model_id}/calibration_report_n010.json"
    raise PromptDevelopmentEvidenceError("development reports support only N=5 or N=10")


def _is_bound_report_path(
    version: str,
    attempt: int,
    count: int,
    model_id: str,
    path: Any,
    *,
    allow_slot_retry: bool = False,
) -> bool:
    if not isinstance(path, str):
        return False
    if path == _expected_report_path(version, attempt, count, model_id):
        return True
    if not allow_slot_retry or version != "v2" or attempt != 1 or count != 10 or model_id != "claude":
        return False
    return re.fullmatch(
        rf"{re.escape(version + '/extensions/n010/slot-retry-')}\d{{3}}/claude/calibration_report_n010\.json",
        path,
    ) is not None


def _report_ref(root: Path, relative: str) -> dict[str, str]:
    return _root_ref(root, relative)


def _screen_model(report: Mapping[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics", {})
    usage = report.get("usage", {})
    trial_metrics = report.get("trial_metrics", [])
    repeated: list[str] = []
    for gate in _GATES:
        failures = sum(not bool(item.get("gates", {}).get(gate, {}).get("initial_passed")) for item in trial_metrics)
        if failures >= 2:
            repeated.append(gate)
    versions = report.get("model_identity", {}).get("versions", [])
    support = report.get("model_identity", {}).get("parameter_support", {})
    identity_stable = all(isinstance(value, list) and len(set(map(str, value))) <= 1 for value in support.values()) and len(set(map(str, versions))) <= 1
    p1 = metrics.get("p1")
    semantic = metrics.get("arch_semantic_first_pass_rate")
    schema_rate = metrics.get("schema_after_format_repair_rate")
    infrastructure = report.get("status") != "complete"
    passed = bool(
        not infrastructure
        and isinstance(p1, (int, float))
        and p1 >= 0.80
        and int(usage.get("truncated", 0)) == 0
    )
    return {
        "p1": p1 if isinstance(p1, (int, float)) else 0.0,
        "schema_after_format_repair_rate": schema_rate if isinstance(schema_rate, (int, float)) else 0.0,
        "arch_semantic_first_pass_rate": semantic if isinstance(semantic, (int, float)) else 0.0,
        "truncations": int(usage.get("truncated", 0)),
        "infrastructure_invalid": infrastructure,
        "repeated_gate_failures": repeated,
        "identity_stable": identity_stable,
        "provider": report.get("model_identity", {}).get("provider"),
        "requested_model": report.get("model_identity", {}).get("model"),
        "returned_versions": list(versions) if isinstance(versions, list) else [],
        "parameter_support": support if isinstance(support, Mapping) else {},
        "screening_pass": passed,
        "cost_usd": float(report.get("usage", {}).get("cost_usd", 0.0)),
        "trial_ids": list(report.get("trials", [])),
    }


def _leave_one_out_sensitive(report: Mapping[str, Any], current_pass: bool) -> bool:
    metrics = report.get("trial_metrics", [])
    if len(metrics) <= 1:
        return False
    for index in range(len(metrics)):
        remaining = metrics[:index] + metrics[index + 1 :]
        schema_rate = sum(bool(item.get("schema_after_format_repair")) for item in remaining) / len(remaining)
        semantic_rate = sum(bool(item.get("semantic_first_pass")) for item in remaining) / len(remaining)
        p1_rate = sum(bool(item.get("p1")) for item in remaining) / len(remaining)
        truncated = sum(int(item.get("usage", {}).get("truncated", 0)) for item in remaining)
        candidate_pass = schema_rate == 1.0 and p1_rate == 1.0 and semantic_rate >= 0.8 and truncated == 0
        if candidate_pass != current_pass:
            return True
    return False


def _assessment_from_reports(
    root: Path,
    version: str,
    attempt: int,
    reports: Mapping[str, Mapping[str, Any]],
    report_refs: Mapping[str, Mapping[str, str]],
    trial_count: int,
    *,
    allow_slot_retry: bool = False,
) -> dict[str, Any]:
    expected_trials = [f"trial_{index:03d}" for index in range(1, trial_count + 1)]
    models: dict[str, Any] = {}
    for model_id in MODEL_IDS:
        report = reports[model_id]
        if (
            report.get("status") != "complete"
            or report.get("lineage_id") != root.name
            or report.get("model_id") != model_id
            or report.get("trial_count") != trial_count
            or list(report.get("trials", [])) != expected_trials
            or [item.get("trial_id") for item in report.get("trial_metrics", [])] != expected_trials
            or not isinstance(report_refs.get(model_id), Mapping)
            or not _is_bound_report_path(
                version, attempt, trial_count, model_id, report_refs[model_id].get("path"),
                allow_slot_retry=allow_slot_retry,
            )
        ):
            raise PromptDevelopmentEvidenceError(f"assessment evidence is not one complete bound report for {model_id}")
        screened = _screen_model(report)
        initial_gate_failures = {
            gate: [
                item["trial_id"]
                for item in report["trial_metrics"]
                if not item["gates"][gate]["initial_passed"]
            ]
            for gate in _GATES
        }
        models[model_id] = {
            "report_ref": dict(report_refs[model_id]),
            **{key: screened[key] for key in ("p1", "schema_after_format_repair_rate", "arch_semantic_first_pass_rate", "truncations", "infrastructure_invalid", "repeated_gate_failures")},
            "screening_pass": screened["screening_pass"],
            "initial_gate_failures": initial_gate_failures,
            "identity_stable": screened["identity_stable"],
            "provider": screened["provider"],
            "requested_model": screened["requested_model"],
            "returned_versions": screened["returned_versions"],
            "parameter_support": screened["parameter_support"],
            "usage": report.get("usage", {}),
            "gates": report.get("gates", {}),
            "repairs": report.get("repairs", {}),
            "tuple": [screened["p1"], screened["arch_semantic_first_pass_rate"], screened["schema_after_format_repair_rate"], -screened["cost_usd"]],
            "trial_ids": screened["trial_ids"],
        }
    screening_pass = all(models[model_id]["screening_pass"] for model_id in MODEL_IDS)
    ambiguity = "none"
    if not screening_pass:
        for model_id in MODEL_IDS:
            screened = _screen_model(reports[model_id])
            if _leave_one_out_sensitive(reports[model_id], screened["screening_pass"]):
                ambiguity = "single_sample_sensitive"
                break
        if ambiguity == "none":
            for model_id in MODEL_IDS:
                metrics = reports[model_id].get("metrics", {})
                p1_ok = metrics.get("p1") == 1.0
                semantic_ok = isinstance(metrics.get("arch_semantic_first_pass_rate"), (int, float)) and metrics["arch_semantic_first_pass_rate"] >= 0.8
                if p1_ok != semantic_ok:
                    ambiguity = "metric_conflict"
                    break
    return {"schema_version": "2.0", "lineage_id": root.name, "version": version, "trial_count": trial_count, "attempt": attempt, "status": "complete", "screening_pass": screening_pass, "ambiguity": ambiguity, "models": models}


def _fallback_tuple(assessment: Mapping[str, Any]) -> tuple[float, float, float, float]:
    models = [assessment["models"][model_id] for model_id in MODEL_IDS]
    return (
        min(float(item["p1"]) for item in models),
        min(float(item["arch_semantic_first_pass_rate"]) for item in models),
        min(float(item["schema_after_format_repair_rate"]) for item in models),
        sum(float(item["tuple"][3]) for item in models),
    )


def _compare_fallback(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> int:
    for index in range(3):
        if left[index] != right[index]:
            return 1 if left[index] > right[index] else -1
    if left[3] != right[3]:
        return 1 if left[3] > right[3] else -1
    return 0


class PromptDevelopmentCoordinator:
    """Stateful facade for one immutable prompt-development lineage."""

    def __init__(
        self,
        development_root: str | Path,
        *,
        config_path: str | Path | None = None,
        context_limits_path: str | Path | None = None,
        provider_factory: Any | None = None,
        prompt_source_path: str | Path | None = None,
        require_environment: bool = False,
        spec_path: str | Path = "gold_file/specIR.json",
        target_path: str | Path | None = None,
        test_bundle_path: str | Path | None = None,
    ) -> None:
        self.root = Path(development_root).resolve()
        self.config_path = Path(config_path).resolve() if config_path is not None else None
        self.context_limits_path = Path(context_limits_path).resolve() if context_limits_path is not None else None
        self.provider_factory = provider_factory
        self.prompt_source_path = Path(prompt_source_path).resolve() if prompt_source_path is not None else None
        self.require_environment = require_environment
        self.spec_path = Path(spec_path).resolve()
        self.target_path = Path(target_path).resolve() if target_path is not None else None
        self.test_bundle_path = Path(test_bundle_path).resolve() if test_bundle_path is not None else None

    def _preflight(self) -> CalibrationPreflight:
        if self.config_path is None or self.context_limits_path is None:
            raise PromptDevelopmentConfigError("development operations require explicit config and context-limits paths")
        preflight = preflight_calibration_config(self.config_path, self.context_limits_path, require_environment=self.require_environment)
        protocol, lineage = _load_protocol(self.root, preflight)
        if protocol["lineage_id"] != lineage["lineage_id"] or protocol["lineage_id"] != self.root.name:
            raise PromptDevelopmentEvidenceError("protocol lineage binding drift")
        return preflight

    @classmethod
    def init(
        cls,
        *,
        config_path: str | Path,
        context_limits_path: str | Path,
        runs_root: str | Path = "runs",
        provider_factory: Any | None = None,
        prompt_source_path: str | Path | None = None,
        require_environment: bool = True,
        spec_path: str | Path = "gold_file/specIR.json",
        target_path: str | Path | None = None,
        test_bundle_path: str | Path | None = None,
    ) -> "PromptDevelopmentCoordinator":
        preflight = preflight_calibration_config(config_path, context_limits_path, require_environment=require_environment)
        prompt = _source_prompt_bytes(Path(prompt_source_path).resolve() if prompt_source_path is not None else None)
        declaration = CalibrationBatchDeclaration(
            trial_count=5, semantic_repair_depth=1, context_window_tokens=preflight.context_limits,
            spec=spec_path, target_profile=target_path or preflight.config.assets.target_profile, test_bundle=test_bundle_path or preflight.config.assets.test_bundle,
        )
        driver = ArchitectureCalibrationDriver(preflight.config, runs_root=runs_root, provider_factory=provider_factory, prompt_bytes=prompt)
        prepared, planning, manifest, constraints = driver._prepare(declaration)
        scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        targets = declaration.targets(preflight.config)
        lineage_store, lineage = driver._publish_lineage(prepared, planning, manifest, constraints, targets, declaration)
        root = lineage_store.root
        config_ref = _publish_bytes(root, "prompt-development/config.json", canonical_json_bytes(preflight.config_snapshot))
        context_ref = _publish_bytes(root, "prompt-development/context_limits.json", preflight.context_bytes)
        protocol = {
            "schema_version": "2.0", "lineage_id": lineage["lineage_id"], "status": "initialized", "model_ids": list(MODEL_IDS), "versions": ["v0", "v1", "v2"],
            "semantic_depth": 1, "base_trial_count": 5, "extension_trial_count": 5, "config_ref": config_ref, "context_limits_ref": context_ref,
            "api_key_env": dict(FIXED_API_KEY_ENVS), "metric_definition": METRIC_DEFINITION,
            "screening": {
                "p1_threshold": 0.80,
                "max_truncations": 0,
                "require_infrastructure_valid": True,
                "reference_p1_threshold": 0.90,
                "reference_relation": "strictly_below",
            },
            "fallback_order": ["min_model_p1", "min_model_arch_semantic_first_pass", "min_model_schema_after_format_repair", "lower_total_cost"],
            "components": {name: dict(ref) for name, ref in lineage["components"].items()},
        }
        _publish_json(root, "prompt-development/protocol.json", protocol, "protocol")
        coordinator = cls(root, config_path=config_path, context_limits_path=context_limits_path, provider_factory=provider_factory, prompt_source_path=prompt_source_path, require_environment=require_environment, spec_path=spec_path, target_path=target_path or preflight.config.assets.target_profile, test_bundle_path=test_bundle_path or preflight.config.assets.test_bundle)
        coordinator._publish_version_snapshot("v0", prompt, previous_version=None)
        return coordinator

    def _frozen_batch_inputs(self, lineage: Mapping[str, Any]) -> tuple[bytes, bytes, bytes]:
        try:
            return tuple(_verify_root_ref(self.root, lineage["inputs"][key], f"lineage/input/{key}") for key in ("spec", "target", "test_bundle"))  # type: ignore[return-value]
        except KeyError as exc:
            raise PromptDevelopmentEvidenceError("lineage is missing a frozen batch input") from exc

    @classmethod
    def init_from_environment(
        cls,
        *,
        runs_root: str | Path = "runs",
        provider_factory: Any | None = None,
        prompt_source_path: str | Path | None = None,
        require_environment: bool = True,
    ) -> "PromptDevelopmentCoordinator":
        config_path = os.environ.get(CONFIG_ENV)
        context_limits_path = os.environ.get(CONTEXT_LIMITS_ENV)
        if not config_path or not context_limits_path:
            raise PromptDevelopmentConfigError(f"{CONFIG_ENV} and {CONTEXT_LIMITS_ENV} must name explicit files")
        return cls.init(
            config_path=config_path,
            context_limits_path=context_limits_path,
            runs_root=runs_root,
            provider_factory=provider_factory,
            prompt_source_path=prompt_source_path,
            require_environment=require_environment,
        )

    def _publish_version_snapshot(self, version: str, prompt: bytes, *, previous_version: str | None) -> dict[str, Any]:
        protocol, lineage = _load_protocol(self.root)
        if version not in protocol["versions"] or (version == "v0" and previous_version is not None) or (version != "v0" and previous_version is None):
            raise PromptDevelopmentError("invalid prompt version ordering")
        scan_prompt_neutrality(prompt)
        version_dir = _version_dir(version)
        prompt_ref = _publish_bytes(self.root, f"{version_dir}/prompt.md", prompt)
        snapshot = {"schema_version": "2.0", "lineage_id": lineage["lineage_id"], "version": version, "prompt_ref": prompt_ref, "prompt_sha256": _sha(prompt), "source_template_sha256": _sha(prompt), "byte_encoding": "utf-8-raw-template"}
        _publish_json(self.root, f"{version_dir}/snapshot.json", snapshot, "snapshot")
        version_record = {"schema_version": "2.0", "lineage_id": lineage["lineage_id"], "version": version, "status": "admitted", "protocol_ref": _root_ref(self.root, "prompt-development/protocol.json"), "prompt_ref": prompt_ref, "prompt_sha256": _sha(prompt), "source_prompt_sha256": _sha(prompt), "semantic_depth": 1, "base_trial_count": 5, "model_ids": list(MODEL_IDS)}
        _publish_json(self.root, f"{version_dir}/version.json", version_record, "version")
        return version_record

    def _check_source_snapshot(self, version_record: Mapping[str, Any], prompt: bytes) -> None:
        source = _source_prompt_bytes(self.prompt_source_path)
        if source != prompt:
            raise PromptDevelopmentEvidenceError("repository prompt source drifted from admitted snapshot")
        if _sha(prompt) != version_record.get("prompt_sha256"):
            raise PromptDevelopmentEvidenceError("repository prompt source drifted from admitted snapshot")
        snapshot = _load_json(self.root, f"{_version_dir(version_record['version'])}/snapshot.json", "snapshot")
        if snapshot.get("lineage_id") != self.root.name or snapshot.get("version") != version_record.get("version"):
            raise PromptDevelopmentEvidenceError("prompt snapshot lineage or version binding drift")
        snapshot_bytes = _verify_root_ref(self.root, snapshot["prompt_ref"], "prompt snapshot bytes")
        if snapshot.get("prompt_sha256") != _sha(snapshot_bytes) or snapshot.get("source_template_sha256") != _sha(snapshot_bytes) or snapshot_bytes != prompt or version_record.get("prompt_ref") != snapshot.get("prompt_ref"):
            raise PromptDevelopmentEvidenceError("prompt snapshot or version binding drift")

    def _next_attempt(self, version: str, *, extension: bool = False) -> int:
        base = f"{_version_dir(version)}/extensions/n010" if extension else f"{_version_dir(version)}/attempts"
        path = self.root / base
        numbers = []
        incomplete = []
        complete_without_assessment = []
        if path.is_dir():
            for child in path.iterdir():
                match = re.fullmatch(r"attempt_(\d{3})", child.name)
                if match:
                    number = int(match.group(1))
                    numbers.append(number)
                    declaration_path = child / "declaration.json"
                    outcome_path = child / "outcome.json"
                    if not declaration_path.is_file():
                        raise PromptDevelopmentEvidenceError(f"attempt {number} has no declaration")
                    declaration = _load_json(self.root, str(declaration_path.relative_to(self.root)), "attempt_declaration")
                    if declaration.get("lineage_id") != self.root.name or declaration.get("version") != version or declaration.get("attempt") != number:
                        raise PromptDevelopmentEvidenceError(f"attempt {number} declaration binding drift")
                    if not outcome_path.is_file():
                        incomplete.append(number)
                    else:
                        outcome = _load_json(self.root, str(outcome_path.relative_to(self.root)), "attempt_outcome")
                        if outcome.get("lineage_id") != self.root.name or outcome.get("version") != version or outcome.get("attempt") != number:
                            raise PromptDevelopmentEvidenceError(f"attempt {number} outcome binding drift")
                        if outcome.get("status") == "complete":
                            assessment_name = "assessment-n010.json" if extension else "assessment-n005.json"
                            if not (self.root / _version_dir(version) / assessment_name).is_file():
                                complete_without_assessment.append(number)
        if numbers and sorted(numbers) != list(range(1, max(numbers) + 1)):
            raise PromptDevelopmentEvidenceError("attempt numbering is not monotonic")
        if len(incomplete) > 1:
            raise PromptDevelopmentEvidenceError("more than one incomplete attempt is committed")
        if incomplete:
            return incomplete[0]
        if len(complete_without_assessment) > 1:
            raise PromptDevelopmentEvidenceError("more than one complete attempt is missing its assessment")
        if complete_without_assessment:
            return complete_without_assessment[0]
        return max(numbers, default=0) + 1

    def _selection_exists(self) -> bool:
        return any(
            (self.root / f"prompt-development/{name}").is_file()
            for name in ("selection.json", "selection-tie.json")
        )

    def _source_guard(self, admitted: bytes) -> Callable[[], None]:
        def guard() -> None:
            if _source_prompt_bytes(self.prompt_source_path) != admitted:
                raise CalibrationDeclarationError("ArchitecturePlanner source drifted during the admitted attempt")
        return guard

    def run_version(self, version: str = "v0") -> dict[str, Any]:
        if self._selection_exists():
            raise PromptDevelopmentError("prompt development is already selected; no later provider I/O is allowed")
        preflight = self._preflight()
        _protocol, lineage = _load_protocol(self.root, preflight)
        planning, constraints = _frozen_planning_context(self.root, lineage)
        if version != "v0":
            previous = "v0" if version == "v1" else "v1"
            previous_assessment = _load_assessment(self.root, previous, 10 if (self.root / f"{_version_dir(previous)}/assessment-n010.json").is_file() else 5)
            if previous_assessment.get("status") != "complete" or previous_assessment.get("screening_pass"):
                raise PromptDevelopmentError("later prompt versions require complete failing prior-version evidence")
            revision_path = self.root / f"{_version_dir(version)}/revision.json"
            if not revision_path.is_file():
                raise PromptDevelopmentError("prompt version has no committed evidence-backed revision")
            revision = _load_json(self.root, str(revision_path.relative_to(self.root)), "revision")
            previous_record = _load_version(self.root, previous)
            if revision.get("lineage_id") != self.root.name or revision.get("previous_prompt_sha256") != previous_record.get("prompt_sha256") or revision.get("new_prompt_sha256") != _load_version(self.root, version).get("prompt_sha256"):
                raise PromptDevelopmentEvidenceError("revision and prompt-version hashes do not agree")
        if (self.root / f"{_version_dir(version)}/assessment-n005.json").is_file() or (self.root / f"{_version_dir(version)}/assessment-n010.json").is_file():
            raise PromptDevelopmentError("a completed prompt version cannot be rerun")
        version_record = _load_version(self.root, version)
        prompt = _verify_root_ref(self.root, version_record["prompt_ref"], f"{version}/prompt")
        self._check_source_snapshot(version_record, prompt)
        scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        attempt = self._next_attempt(version)
        attempt_dir = f"{_version_dir(version)}/attempts/attempt_{attempt:03d}"
        declaration = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "attempt": attempt, "status": "declared", "prompt_ref": dict(version_record["prompt_ref"]), "prompt_sha256": version_record["prompt_sha256"], "trial_count": 5, "semantic_depth": 1, "model_ids": list(MODEL_IDS)}
        _publish_json(self.root, f"{attempt_dir}/declaration.json", declaration, "attempt_declaration")
        driver = ArchitectureCalibrationDriver(preflight.config, runs_root=self.root.parents[2], provider_factory=self.provider_factory, prompt_bytes=prompt, prompt_source_guard=self._source_guard(prompt))
        spec_bytes, target_bytes, bundle_bytes = self._frozen_batch_inputs(lineage)
        batch = CalibrationBatchDeclaration(
            prompt_version=version, trial_count=5, semantic_repair_depth=1, context_window_tokens=preflight.context_limits,
            spec=spec_bytes, target_profile=target_bytes, test_bundle=bundle_bytes, attempt=attempt,
        )
        reports: dict[str, dict[str, Any]] = {}
        report_refs: dict[str, dict[str, str]] = {}
        status = "complete"
        try:
            driver.run(batch)
            self._check_source_snapshot(version_record, prompt)
            for model_id in MODEL_IDS:
                model_root = self.root / version / (f"attempt_{attempt:03d}/" if attempt > 1 else "") / model_id
                report = recompute_calibration_report(model_root, config=preflight.config)
                reports[model_id] = report
                report_path = model_root / "calibration_report.json"
                report_refs[model_id] = _report_ref(self.root, str(report_path.relative_to(self.root))) if report_path.is_file() else None
                if report.get("status") != "complete":
                    status = "infrastructure-invalid"
            self._check_source_snapshot(version_record, prompt)
            if status != "complete":
                raise PromptDevelopmentEvidenceError("one or more model reports are infrastructure-invalid")
            assessment = _assessment_from_reports(self.root, version, attempt, reports, report_refs, 5)
        except (CalibrationError, PromptDevelopmentEvidenceError) as exc:
            failed_refs = dict(report_refs)
            for model_id in MODEL_IDS:
                model_root = self.root / version / (f"attempt_{attempt:03d}/" if attempt > 1 else "") / model_id
                existing = model_root / "calibration_report.json"
                if existing.is_file():
                    failed_refs[model_id] = _report_ref(self.root, str(existing.relative_to(self.root)))
            failed_outcome = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "attempt": attempt, "status": "infrastructure-invalid", "reports": {name: failed_refs.get(name) for name in MODEL_IDS}}
            _publish_json(self.root, f"{attempt_dir}/outcome.json", failed_outcome, "attempt_outcome")
            raise PromptDevelopmentError(f"prompt development attempt {attempt} failed before complete evidence: {type(exc).__name__}") from exc
        outcome = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "attempt": attempt, "status": status, "reports": report_refs}
        outcome_ref = _publish_json(self.root, f"{attempt_dir}/outcome.json", outcome, "attempt_outcome")
        if status != "complete":
            return outcome
        assessment_ref = _publish_json(self.root, f"{_version_dir(version)}/assessment-n005.json", assessment, "assessment")
        version_outcome = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "status": "complete", "assessment_ref": assessment_ref, "conclusion": "passed screening" if assessment["screening_pass"] else "failed screening"}
        _publish_json(self.root, f"{_version_dir(version)}/outcome.json", version_outcome, "outcome")
        if assessment["screening_pass"]:
            self.select(version, assessment=assessment, assessment_ref=assessment_ref, reason="first passing version")
        elif version == "v2":
            # The fixed fallback is resolved only after the complete V0/V1/V2
            # set exists.  A tie is a terminal, explicit outcome, not an
            # exception that leaves the lineage looking unfinished.
            self.select(version, assessment=assessment, assessment_ref=assessment_ref, reason="complete fallback comparison")
        return {"outcome": outcome, "assessment": assessment, "outcome_ref": outcome_ref}

    def record_revision(
        self,
        version: str,
        *,
        hypothesis: str,
        evidence_refs: list[Mapping[str, Any]],
        expected_gates: list[str] | None = None,
        expected_metrics: list[str] | None = None,
        prompt_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        if self._selection_exists():
            raise PromptDevelopmentError("selected prompt cannot be edited")
        if version not in {"v1", "v2"}:
            raise PromptDevelopmentError("only v1 and v2 may contain revisions")
        preflight = preflight_calibration_config(
            self.config_path,
            self.context_limits_path,
            require_environment=self.require_environment,
        )
        _protocol, lineage = _load_protocol(self.root)
        planning, constraints = _frozen_planning_context(self.root, lineage)
        previous = "v0" if version == "v1" else "v1"
        previous_count = 10 if (self.root / f"{_version_dir(previous)}/assessment-n010.json").is_file() else 5
        previous_assessment = _load_assessment(self.root, previous, previous_count)
        if previous_assessment.get("status") != "complete" or previous_assessment.get("screening_pass"):
            raise PromptDevelopmentError("revision requires complete failing prior-version assessment")
        if not isinstance(hypothesis, str) or not hypothesis.strip() or " and " in hypothesis.casefold():
            raise PromptDevelopmentError("revision must contain exactly one focused hypothesis")
        if not evidence_refs:
            raise PromptDevelopmentError("revision requires exact evidence references")
        if version == "v2":
            previous_revision = _load_json(self.root, f"{_version_dir(previous)}/revision.json", "revision")
            if previous_revision.get("hypothesis") == hypothesis.strip():
                raise PromptDevelopmentError("v2 hypothesis must be distinct from v1")
        evidence_paths: set[str] = set()
        for ref in evidence_refs:
            ref_path = str(ref.get("path", ""))
            if ref_path in evidence_paths:
                raise PromptDevelopmentEvidenceError("revision evidence references must be unique")
            evidence_paths.add(ref_path)
            if not ref_path.startswith(_version_dir(previous) + "/"):
                raise PromptDevelopmentEvidenceError("revision evidence must belong to the immediately previous version")
            _verify_root_ref(self.root, ref, "revision evidence")
            if ref_path.endswith("assessment-n005.json") or ref_path.endswith("assessment-n010.json"):
                cited = _load_json(self.root, ref_path, "assessment")
                if cited.get("status") != "complete" or cited.get("screening_pass") is not False:
                    raise PromptDevelopmentEvidenceError("revision evidence must cite a complete failing assessment")
            elif "/calibration_report.json" in ref_path or ref_path.endswith("calibration_report_n010.json"):
                cited = _read_json_ref(self.root, ref, "revision report evidence")
                if cited.get("lineage_id") != self.root.name or cited.get("prompt_version") != previous or _screen_model(cited)["screening_pass"]:
                    raise PromptDevelopmentEvidenceError("revision report evidence does not identify a failing prior report")
            elif "/trials/trial_" not in ref_path:
                raise PromptDevelopmentEvidenceError("revision evidence must cite the prior assessment, report, or a failing trial")
        gates = list(expected_gates or [])
        metrics = list(expected_metrics or ["arch_semantic_first_pass_rate"])
        if len(gates) != len(set(gates)) or any(gate not in _GATES for gate in gates):
            raise PromptDevelopmentError("revision expected_gates must contain only unique ARCH_VALIDATE gates")
        if len(metrics) != len(set(metrics)) or any(metric not in _METRIC_NAMES for metric in metrics):
            raise PromptDevelopmentError("revision expected_metrics contains an unsupported screening metric")
        proposed = prompt_bytes if prompt_bytes is not None else _source_prompt_bytes(self.prompt_source_path)
        if _source_prompt_bytes(self.prompt_source_path) != proposed:
            raise PromptDevelopmentEvidenceError("proposed revision bytes do not match the repository prompt source")
        scan_prompt_neutrality(proposed, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        old = _verify_root_ref(self.root, _load_version(self.root, previous)["prompt_ref"], "previous prompt")
        if proposed == old:
            raise PromptDevelopmentError("revision must change prompt bytes")
        diff = "".join(difflib.unified_diff(old.decode("utf-8").splitlines(True), proposed.decode("utf-8").splitlines(True), fromfile=previous, tofile=version))
        revision = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "previous_version": previous, "previous_prompt_sha256": _sha(old), "new_prompt_sha256": _sha(proposed), "hypothesis": hypothesis.strip(), "evidence_refs": [dict(ref) for ref in evidence_refs], "expected_gates": gates, "expected_metrics": metrics, "unified_diff": diff, "protocol_ref": _root_ref(self.root, "prompt-development/protocol.json"), "status": "admitted"}
        _publish_json(self.root, f"{_version_dir(version)}/revision.json", revision, "revision")
        return self._publish_version_snapshot(version, proposed, previous_version=previous)

    def expand(self, version: str) -> dict[str, Any]:
        if self._selection_exists():
            raise PromptDevelopmentError("selected prompt cannot be expanded")
        if version not in {"v1", "v2"}:
            raise PromptDevelopmentError("only v1 and v2 may be extended")
        preflight = self._preflight()
        _protocol, lineage = _load_protocol(self.root, preflight)
        if (self.root / f"{_version_dir(version)}/assessment-n010.json").is_file():
            raise PromptDevelopmentError("a version may have at most one N=10 extension")
        assessment = _load_assessment(self.root, version, 5)
        if assessment.get("status") != "complete" or assessment.get("ambiguity") not in {"single_sample_sensitive", "metric_conflict"}:
            raise PromptDevelopmentError("N=10 expansion requires an exact evidence-backed ambiguity")
        version_record = _load_version(self.root, version)
        prompt = _verify_root_ref(self.root, version_record["prompt_ref"], "extension prompt")
        self._check_source_snapshot(version_record, prompt)
        extension_attempt = self._next_attempt(version, extension=True)
        extension_dir = f"{_version_dir(version)}/extensions/n010"
        extension_path = f"{_version_dir(version)}/extension.json"
        proposed_extension = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "status": "admitted", "reason": assessment["ambiguity"], "base_refs": {model_id: dict(assessment["models"][model_id]["report_ref"]) for model_id in MODEL_IDS}, "trial_ids": [f"trial_{index:03d}" for index in range(6, 11)], "prompt_sha256": version_record["prompt_sha256"], "semantic_depth": 1, "attempt": extension_attempt}
        if (self.root / extension_path).is_file():
            extension = _load_json(self.root, extension_path, "extension")
            for field in ("lineage_id", "version", "reason", "base_refs", "trial_ids", "prompt_sha256", "semantic_depth"):
                if extension.get(field) != proposed_extension[field]:
                    raise PromptDevelopmentEvidenceError(f"immutable N10 extension binding drift: {field}")
        else:
            extension = proposed_extension
            _publish_json(self.root, extension_path, extension, "extension")
        extension_attempt_dir = f"{extension_dir}/attempt_{extension_attempt:03d}"
        extension_declaration = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "attempt": extension_attempt, "status": "declared", "prompt_ref": dict(version_record["prompt_ref"]), "prompt_sha256": version_record["prompt_sha256"], "trial_count": 5, "semantic_depth": 1, "model_ids": list(MODEL_IDS)}
        _publish_json(self.root, f"{extension_attempt_dir}/declaration.json", extension_declaration, "attempt_declaration")
        driver = ArchitectureCalibrationDriver(preflight.config, runs_root=self.root.parents[2], provider_factory=self.provider_factory, prompt_bytes=prompt, prompt_source_guard=self._source_guard(prompt), publish_reports=False)
        spec_bytes, target_bytes, bundle_bytes = self._frozen_batch_inputs(lineage)
        batch = CalibrationBatchDeclaration(
            prompt_version=version, trial_count=5, semantic_repair_depth=1, context_window_tokens=preflight.context_limits,
            spec=spec_bytes, target_profile=target_bytes, test_bundle=bundle_bytes,
            attempt=extension_attempt, batch_kind="extension", trial_start=6, trial_ids=tuple(f"trial_{index:03d}" for index in range(6, 11)), root_relative_path=f"{version}/extensions/n010" if extension_attempt == 1 else f"{version}/extensions/n010/attempt_{extension_attempt:03d}",
        )
        combined_reports: dict[str, dict[str, Any]] = {}
        combined_refs: dict[str, dict[str, str]] = {}
        try:
            driver.run(batch)
            self._check_source_snapshot(version_record, prompt)
            for model_id in MODEL_IDS:
                base_root = self.root / version / (f"attempt_{assessment['attempt']:03d}/" if assessment["attempt"] > 1 else "") / model_id
                ext_root = self.root / version / "extensions" / "n010" / (f"attempt_{extension_attempt:03d}" if extension_attempt > 1 else "") / model_id
                base_report = recompute_calibration_report(base_root, config=preflight.config)
                extension_report = recompute_calibration_report(ext_root, config=preflight.config)
                combined = _combine_reports(base_report, extension_report)
                errors = sorted(Draft202012Validator(load_schema("calibration-report.schema.json")).iter_errors(combined), key=lambda item: tuple(item.absolute_path))
                if errors:
                    raise PromptDevelopmentEvidenceError(f"invalid N10 report for {model_id}: {errors[0].message}")
                combined_reports[model_id] = combined
                combined_refs[model_id] = _publish_bytes(self.root, str(ext_root.relative_to(self.root) / "calibration_report_n010.json"), canonical_json_bytes(combined))
            self._check_source_snapshot(version_record, prompt)
            assessment_n10 = _assessment_from_reports(self.root, version, extension_attempt, combined_reports, combined_refs, 10)
        except (CalibrationError, PromptDevelopmentEvidenceError) as exc:
            invalid_outcome = {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "attempt": extension_attempt, "status": "infrastructure-invalid", "reports": {model_id: combined_refs.get(model_id) for model_id in MODEL_IDS}}
            _publish_json(self.root, f"{extension_attempt_dir}/outcome.json", invalid_outcome, "attempt_outcome")
            raise PromptDevelopmentError(f"N10 extension attempt {extension_attempt} failed before complete evidence: {type(exc).__name__}") from exc
        _publish_json(self.root, f"{extension_attempt_dir}/outcome.json", {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "attempt": extension_attempt, "status": "complete", "reports": combined_refs}, "attempt_outcome")
        assessment_ref = _publish_json(self.root, f"{_version_dir(version)}/assessment-n010.json", assessment_n10, "assessment")
        _publish_json(self.root, f"{_version_dir(version)}/outcome.json", {"schema_version": "2.0", "lineage_id": self.root.name, "version": version, "status": "complete", "assessment_ref": assessment_ref, "conclusion": "passed screening" if assessment_n10["screening_pass"] else "failed screening"}, "outcome")
        if assessment_n10["screening_pass"]:
            self.select(version, assessment=assessment_n10, assessment_ref=assessment_ref, reason="first passing final assessment")
        return {"extension": extension, "assessment": assessment_n10, "assessment_ref": assessment_ref}

    def retry_extension_slot(
        self,
        version: str = "v2",
        *,
        model_id: str = "claude",
        trial_id: str = "trial_010",
    ) -> dict[str, Any]:
        """Run the explicitly authorized single-slot N=10 exception.

        The original invalid trial and the other model-slot leaves remain
        immutable.  Complete Claude extension leaves are copied into a
        separately named retry root, then the one missing trial is executed
        against the unchanged lineage controls.  The resulting report is
        allowed only through the narrowly bound exception path below.
        """

        if self._selection_exists():
            raise PromptDevelopmentError("prompt development is already selected; no later provider I/O is allowed")
        if (version, model_id, trial_id) != ("v2", "claude", "trial_010"):
            raise PromptDevelopmentError("the authorized exception is limited to v2/claude/trial_010")
        preflight = preflight_calibration_config(
            self.config_path,
            self.context_limits_path,
            require_environment=self.require_environment,
        )
        _protocol, lineage = _load_protocol(self.root)
        assessment = _load_assessment(self.root, version, 5)
        if assessment.get("status") != "complete" or assessment.get("screening_pass"):
            raise PromptDevelopmentError("the slot retry requires a complete failing V2 N=5 assessment")
        version_record = _load_version(self.root, version)
        prompt = _verify_root_ref(self.root, version_record["prompt_ref"], "slot retry prompt")
        self._check_source_snapshot(version_record, prompt)

        extension = _load_json(self.root, f"{_version_dir(version)}/extension.json", "extension")
        if extension.get("attempt") != 1 or extension.get("trial_ids") != [f"trial_{index:03d}" for index in range(6, 11)]:
            raise PromptDevelopmentEvidenceError("slot retry is not bound to the original N=10 extension")
        original_root = self.root / version / "extensions" / "n010" / model_id
        original_report_path = f"{version}/extensions/n010/{model_id}/calibration_report_n010.json"
        original_report_ref = _root_ref(self.root, original_report_path)
        original_report = _read_json_ref(self.root, original_report_ref, "original invalid slot report")
        if original_report.get("status") != "infrastructure-invalid" or original_report.get("trial_count") != 10:
            raise PromptDevelopmentEvidenceError("slot retry requires the committed invalid Claude N=10 report")
        retry_relative = f"{version}/extensions/n010/slot-retry-001"
        retry_root = self.root / retry_relative / model_id
        retry_trial_root = retry_root / "trials" / trial_id
        retry_trial_complete = all((retry_trial_root / name).is_file() for name in ("request_ref.json", "response_ref.json", "validation.json"))
        retry_staging = list((retry_root / "trials").glob("*.staging")) if (retry_root / "trials").is_dir() else []
        if retry_root.exists() and any(retry_root.iterdir()) and (not retry_trial_complete or retry_staging):
            raise PromptDevelopmentEvidenceError("the authorized slot retry root contains incomplete evidence")
        reused_trial_ids = [f"trial_{index:03d}" for index in range(6, 10)]
        source_trial_refs = {
            trial: _root_ref(self.root, f"{original_root.relative_to(self.root).as_posix()}/trials/{trial}/validation.json")
            for trial in reused_trial_ids
        }
        exception_record = {
            "schema_version": "2.0",
            "lineage_id": self.root.name,
            "version": version,
            "extension_attempt": 1,
            "status": "admitted",
            "exception": "single_slot_retry",
            "authorization": "explicit_user_authorization",
            "model_id": model_id,
            "trial_id": trial_id,
            "original_invalid_report_ref": original_report_ref,
            "reused_trial_ids": reused_trial_ids,
            "reused_trial_validation_refs": source_trial_refs,
            "retry_root": f"{retry_relative}/{model_id}",
        }
        exception_ref = RunStore(self.root).publish_immutable_json(f"{retry_relative}/exception.json", exception_record)
        preflight = self._preflight()
        _protocol, lineage = _load_protocol(self.root, preflight)
        original_extension = _recompute_lineage_report(self.root, original_root, config=preflight.config)
        original_base = _recompute_lineage_report(self.root, self.root / version / model_id, config=preflight.config)
        original_recomputed = _combine_reports(original_base, original_extension)
        if original_recomputed.get("status") != "infrastructure-invalid" or original_recomputed != original_report:
            raise PromptDevelopmentEvidenceError("original invalid Claude report is not deterministic evidence")

        if not retry_trial_complete:
            retry_root.mkdir(parents=True, exist_ok=True)
            for source_path in original_root.rglob("*"):
                if not source_path.is_file():
                    continue
                relative = source_path.relative_to(original_root)
                if relative == Path("batch.json") or relative == Path("calibration_report_n010.json"):
                    continue
                if len(relative.parts) >= 2 and relative.parts[0] == "trials" and relative.parts[1] == trial_id:
                    continue
                RunStore(retry_root).publish_immutable_bytes(str(relative), source_path.read_bytes())

        frozen_spec, frozen_target, frozen_bundle = self._frozen_batch_inputs(lineage)
        declaration = CalibrationBatchDeclaration(
            prompt_version=version,
            trial_count=5,
            semantic_repair_depth=1,
            context_window_tokens=preflight.context_limits,
            spec=frozen_spec,
            target_profile=frozen_target,
            test_bundle=frozen_bundle,
            attempt=1,
            batch_kind="extension",
            trial_start=6,
            trial_ids=tuple(f"trial_{index:03d}" for index in range(6, 11)),
            root_relative_path=retry_relative,
        )
        driver = ArchitectureCalibrationDriver(
            preflight.config,
            runs_root=self.root.parents[2],
            provider_factory=self.provider_factory,
            prompt_bytes=prompt,
            prompt_source_guard=self._source_guard(prompt),
            publish_reports=False,
        )
        _prepared, planning, manifest, constraints = driver._prepare(declaration)
        targets = declaration.targets(preflight.config)
        lineage_store = RunStore(self.root)
        recorded_components = {
            name: _verify_root_ref(self.root, ref, f"lineage/components/{name}")
            for name, ref in lineage.get("components", {}).items()
        }
        original_components = _architecture._default_components
        _architecture._default_components = lambda: dict(recorded_components)
        try:
            driver._model_worker(
                lineage_store,
                lineage,
                model_id,
                targets[model_id],
                declaration,
                planning,
                manifest,
                constraints,
            )
        finally:
            _architecture._default_components = original_components
        extension_report = _recompute_lineage_report(self.root, retry_root, config=preflight.config)
        if extension_report.get("status") != "complete":
            failure = {
                "schema_version": "2.0",
                "lineage_id": self.root.name,
                "version": version,
                "status": "infrastructure-invalid",
                "exception_ref": exception_ref.as_dict(),
                "report_ref": None,
            }
            RunStore(self.root).publish_immutable_json(f"{retry_relative}/failure.json", failure)
            raise PromptDevelopmentError("authorized Claude slot retry remained infrastructure-invalid")
        retry_extension_ref = RunStore(retry_root).publish_immutable_json(
            "calibration_report.json", extension_report, schema_name="calibration-report.schema.json"
        )
        combined_claude = _combine_reports(
            _recompute_lineage_report(self.root, self.root / version / model_id, config=preflight.config),
            extension_report,
        )
        combined_claude_ref = RunStore(self.root).publish_immutable_json(
            f"{retry_relative}/{model_id}/calibration_report_n010.json",
            combined_claude,
            schema_name="calibration-report.schema.json",
        )

        reports: dict[str, dict[str, Any]] = {model_id: combined_claude}
        report_refs: dict[str, dict[str, str]] = {model_id: combined_claude_ref.as_dict()}
        for other_model in ("qwen", "deepseek"):
            other_root = self.root / version / "extensions" / "n010" / other_model
            other_report_path = f"{version}/extensions/n010/{other_model}/calibration_report_n010.json"
            other_report_ref = _root_ref(self.root, other_report_path)
            other_report = _read_json_ref(self.root, other_report_ref, f"existing {other_model} N=10 report")
            expected_other = _combine_reports(
                _recompute_lineage_report(self.root, self.root / version / other_model, config=preflight.config),
                _recompute_lineage_report(self.root, other_root, config=preflight.config),
            )
            if other_report != expected_other or other_report.get("status") != "complete":
                raise PromptDevelopmentEvidenceError(f"existing {other_model} N=10 evidence is not deterministic")
            reports[other_model] = other_report
            report_refs[other_model] = other_report_ref
        assessment_n10 = _assessment_from_reports(
            self.root,
            version,
            1,
            reports,
            report_refs,
            10,
            allow_slot_retry=True,
        )
        assessment_ref = _publish_json(self.root, f"{_version_dir(version)}/assessment-n010.json", assessment_n10, "assessment")
        completion = {
            "schema_version": "2.0",
            "lineage_id": self.root.name,
            "version": version,
            "status": "complete",
            "exception_ref": exception_ref.as_dict(),
            "retry_extension_report_ref": retry_extension_ref.as_dict(),
            "combined_report_ref": combined_claude_ref.as_dict(),
            "assessment_ref": assessment_ref,
        }
        completion_ref = RunStore(self.root).publish_immutable_json(f"{retry_relative}/completion.json", completion)
        selection = self.select(
            version,
            assessment=assessment_n10,
            assessment_ref=assessment_ref,
            reason="first passing version or fallback after authorized single-slot Claude retry exception",
        )
        return {
            "status": "complete",
            "exception_ref": exception_ref.as_dict(),
            "completion_ref": completion_ref.as_dict(),
            "assessment_ref": assessment_ref,
            "selection": selection,
        }

    def recompute(self, version: str, count: int = 5, *, require_complete: bool = False, require_source_match: bool = False) -> dict[str, Any]:
        preflight = self._preflight()
        assessment = _load_assessment(self.root, version, count)
        if require_complete:
            if assessment.get("status") != "complete" or not (self.root / "prompt-development/selection.json").is_file():
                raise PromptDevelopmentEvidenceError("complete recomputation requires a committed complete assessment and selection")
            selection = _load_json(self.root, "prompt-development/selection.json", "selection")
            assessment_path = f"{_version_dir(version)}/assessment-n{count:03d}.json"
            if selection.get("selected_version") != version or selection.get("assessment_ref", {}).get("path") != assessment_path:
                raise PromptDevelopmentEvidenceError("selection is not bound to the requested final assessment")
            _verify_root_ref(self.root, selection["assessment_ref"], "selection assessment")
            selected_record = _load_version(self.root, version)
            if selection.get("prompt_ref") != selected_record.get("prompt_ref") or selection.get("prompt_sha256") != selected_record.get("prompt_sha256"):
                raise PromptDevelopmentEvidenceError("selection prompt binding drift")
        if require_source_match:
            version_record = _load_version(self.root, version)
            prompt = _verify_root_ref(self.root, version_record["prompt_ref"], "recompute prompt")
            self._check_source_snapshot(version_record, prompt)
        rebuilt_reports: dict[str, dict[str, Any]] = {}
        report_refs: dict[str, dict[str, str]] = {}
        for model_id in MODEL_IDS:
            report_ref = assessment["models"][model_id]["report_ref"]
            report = _read_json_ref(self.root, report_ref, f"assessment/{model_id}/report")
            if report.get("lineage_id") != self.root.name or report.get("prompt_version") != version or report.get("trial_count") != count:
                raise PromptDevelopmentEvidenceError("assessment report binding drift")
            if count == 5:
                model_root = self.root / version / (f"attempt_{assessment['attempt']:03d}/" if assessment["attempt"] > 1 else "") / model_id
                rebuilt = _recompute_lineage_report(self.root, model_root, config=preflight.config)
            else:
                ext_attempt = int(assessment["attempt"])
                standard_path = _expected_report_path(version, ext_attempt, count, model_id)
                report_path = str(report_ref.get("path", ""))
                if report_path == standard_path:
                    model_root = self.root / version / "extensions" / "n010" / (f"attempt_{ext_attempt:03d}/" if ext_attempt > 1 else "") / model_id
                else:
                    model_root = self.root / Path(report_path).parent
                base_assessment = _load_assessment(self.root, version, 5)
                base_root = self.root / version / (f"attempt_{base_assessment['attempt']:03d}/" if base_assessment["attempt"] > 1 else "") / model_id
                rebuilt = _combine_reports(
                    _recompute_lineage_report(self.root, base_root, config=preflight.config),
                    _recompute_lineage_report(self.root, model_root, config=preflight.config),
                )
            if rebuilt != report:
                raise PromptDevelopmentEvidenceError("assessment report is not deterministic evidence")
            rebuilt_reports[model_id] = rebuilt
            report_refs[model_id] = dict(report_ref)
        rebuilt_assessment = _assessment_from_reports(
            self.root,
            version,
            int(assessment["attempt"]),
            rebuilt_reports,
            report_refs,
            count,
            allow_slot_retry=True,
        )
        if rebuilt_assessment != assessment:
            raise PromptDevelopmentEvidenceError("assessment summary is not a deterministic projection of report evidence")
        tie_path = self.root / "prompt-development/selection-tie.json"
        if tie_path.is_file():
            tie = _load_json(self.root, "prompt-development/selection-tie.json", "selection")
            expected_tuples: dict[str, list[float]] = {}
            for candidate in ("v0", "v1", "v2"):
                candidate_ref = tie.get("assessment_refs", {}).get(candidate)
                if not isinstance(candidate_ref, Mapping):
                    raise PromptDevelopmentEvidenceError("selection tie is missing an assessment reference")
                candidate_assessment = _read_json_ref(self.root, candidate_ref, f"selection tie/{candidate}/assessment")
                expected_tuples[candidate] = list(_fallback_tuple(candidate_assessment))
            if tie.get("status") != "selection-tie" or tie.get("comparison_tuples") != expected_tuples:
                raise PromptDevelopmentEvidenceError("selection tie is not a deterministic fallback projection")
            if require_complete:
                raise PromptDevelopmentEvidenceError("a selection tie cannot satisfy complete selection recomputation")
        return assessment

    def select(self, version: str, *, assessment: Mapping[str, Any] | None = None, assessment_ref: Mapping[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
        if self._selection_exists():
            tie_path = self.root / "prompt-development/selection-tie.json"
            if tie_path.is_file() and not (self.root / "prompt-development/selection.json").is_file():
                return _load_json(self.root, "prompt-development/selection-tie.json", "selection")
            selection = _load_json(self.root, "prompt-development/selection.json", "selection")
            if selection.get("lineage_id") != self.root.name:
                raise PromptDevelopmentEvidenceError("selection lineage binding drift")
            selected_version = selection.get("selected_version")
            selected_record = _load_version(self.root, selected_version)
            selected_prompt = _verify_root_ref(self.root, selection["prompt_ref"], "selection prompt")
            if selection.get("prompt_ref") != selected_record.get("prompt_ref") or selection.get("prompt_sha256") != _sha(selected_prompt):
                raise PromptDevelopmentEvidenceError("selection prompt reference or hash drift")
            assessment_path = selection.get("assessment_ref", {}).get("path")
            if not isinstance(assessment_path, str) or not assessment_path.startswith(_version_dir(selected_version) + "/assessment-n"):
                raise PromptDevelopmentEvidenceError("selection assessment reference drift")
            selected_count = int(Path(assessment_path).stem.removeprefix("assessment-n"))
            _verify_root_ref(self.root, selection["assessment_ref"], "selection assessment")
            self.recompute(selected_version, selected_count)
            expected_tuples: dict[str, list[float]] = {}
            for candidate in selection.get("comparison_tuples", {}):
                candidate_path = next((self.root / f"{_version_dir(candidate)}/assessment-n{count:03d}.json" for count in (10, 5) if (self.root / f"{_version_dir(candidate)}/assessment-n{count:03d}.json").is_file()), None)
                if candidate_path is None:
                    raise PromptDevelopmentEvidenceError("selection contains an assessment tuple without evidence")
                candidate_count = 10 if candidate_path.name == "assessment-n010.json" else 5
                candidate_assessment = self.recompute(candidate, candidate_count)
                expected_tuples[candidate] = list(_fallback_tuple(candidate_assessment))
            if selection.get("comparison_tuples") != expected_tuples:
                raise PromptDevelopmentEvidenceError("selection comparison tuples are not recomputable")
            self._check_source_snapshot(selected_record, selected_prompt)
            self._publish_development_handoff(selection)
            return selection
        assessments: dict[str, dict[str, Any]] = {}
        for candidate in ("v0", "v1", "v2"):
            for count in (10, 5):
                path = self.root / f"{_version_dir(candidate)}/assessment-n{count:03d}.json"
                if path.is_file():
                    assessments[candidate] = _load_json(self.root, str(path.relative_to(self.root)), "assessment")
                    break
        if assessment is not None:
            assessments[version] = dict(assessment)
        if not assessments or version not in assessments:
            raise PromptDevelopmentError("selection requires a committed assessment")
        for candidate, value in list(assessments.items()):
            assessments[candidate] = self.recompute(candidate, int(value["trial_count"]))
        chosen: str | None = None
        for candidate in ("v0", "v1", "v2"):
            if candidate in assessments and assessments[candidate].get("screening_pass"):
                chosen = candidate
                break
        if chosen is None and all(candidate in assessments and assessments[candidate].get("status") == "complete" for candidate in ("v0", "v1", "v2")):
            tuples = {candidate: _fallback_tuple(assessments[candidate]) for candidate in assessments}
            best = max(tuples.values(), key=lambda item: item)
            winners = [candidate for candidate, value in tuples.items() if value == best]
            if len(winners) != 1:
                tie = {
                    "schema_version": "2.0", "lineage_id": self.root.name,
                    "status": "selection-tie", "selected_version": None,
                    "prompt_ref": None, "prompt_sha256": None,
                    "assessment_ref": None,
                    "reason": "PROMPT_SELECTION_TIE",
                    "comparison_tuples": tuples,
                    "assessment_refs": {
                        candidate: _root_ref(self.root, f"{_version_dir(candidate)}/assessment-n{value['trial_count']:03d}.json")
                        for candidate, value in assessments.items()
                    },
                }
                _publish_json(self.root, "prompt-development/selection-tie.json", tie, "selection")
                return tie
            chosen = winners[0]
        else:
            return {"status": "not-ready"}
        if chosen is None:
            return {"status": "not-ready"}
        selected_assessment = assessments[chosen]
        version_record = _load_version(self.root, chosen)
        prompt = _verify_root_ref(self.root, version_record["prompt_ref"], "selected prompt")
        self._check_source_snapshot(version_record, prompt)
        count = selected_assessment["trial_count"]
        assessment_path = f"{_version_dir(chosen)}/assessment-n{count:03d}.json"
        tuples = {candidate: list(_fallback_tuple(value)) for candidate, value in assessments.items()}
        selection = {"schema_version": "2.0", "lineage_id": self.root.name, "status": "selected", "selected_version": chosen, "prompt_ref": dict(version_record["prompt_ref"]), "prompt_sha256": version_record["prompt_sha256"], "assessment_ref": _root_ref(self.root, assessment_path), "reason": reason or ("first passing version" if selected_assessment.get("screening_pass") else "authoritative fallback tuple"), "comparison_tuples": tuples, "assessment_refs": {candidate: _root_ref(self.root, f"{_version_dir(candidate)}/assessment-n{value['trial_count']:03d}.json") for candidate, value in assessments.items()}}
        _publish_json(self.root, "prompt-development/selection.json", selection, "selection")
        self._publish_development_handoff(selection)
        return selection

    def _publish_development_handoff(self, selection: Mapping[str, Any]) -> dict[str, str]:
        if selection.get("status") != "selected":
            raise PromptDevelopmentError("only a selected development prompt can produce a handoff")
        selected_version = selection.get("selected_version")
        prompt_ref = selection.get("prompt_ref")
        assessment_ref = selection.get("assessment_ref")
        if selected_version not in {"v0", "v1", "v2"} or not isinstance(prompt_ref, Mapping) or not isinstance(assessment_ref, Mapping):
            raise PromptDevelopmentEvidenceError("selected development record is not handoff-complete")
        handoff = {
            "schema_version": "2.0",
            "lineage_id": self.root.name,
            "consumer": "m1-4a3",
            "selection_ref": _root_ref(self.root, "prompt-development/selection.json"),
            "selected_version": selected_version,
            "prompt_ref": dict(prompt_ref),
            "prompt_sha256": selection.get("prompt_sha256"),
            "assessment_ref": dict(assessment_ref),
            "selection_reason": selection.get("reason"),
            "satisfies": {
                "m1_4a3_admission": True,
                "n10_qualification": False,
                "b1_b4": False,
                "production_freeze": False,
                "owner_signature": False,
                "formal_run": False,
                "s5_s6": False,
            },
        }
        return _publish_json(self.root, "prompt-development/handoff.json", handoff, "handoff")

    def next_action(self, version: str) -> dict[str, Any]:
        if (self.root / "prompt-development/selection.json").is_file():
            return {"action": "terminal-selection"}
        if (self.root / "prompt-development/selection-tie.json").is_file():
            return {"action": "terminal-tie"}
        version_dir = self.root / _version_dir(version) / "attempts"
        outcomes = sorted(version_dir.glob("attempt_*/outcome.json")) if version_dir.is_dir() else []
        if not outcomes:
            return {"action": "run", "attempt": 1}
        latest = _load_json(self.root, str(outcomes[-1].relative_to(self.root)), "attempt_outcome")
        if latest["status"] == "infrastructure-invalid":
            return {"action": "run", "attempt": int(latest["attempt"]) + 1}
        return {"action": "assess", "attempt": latest["attempt"]}


def build_development_summary(
    development_root: str | Path,
    *,
    config_path: str | Path,
    context_limits_path: str | Path,
) -> dict[str, Any]:
    """Build the tracked M1-4a2 summary solely from one new lineage's leaves."""

    root = Path(development_root).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", root.name):
        raise PromptDevelopmentEvidenceError("development report requires a lineage root")
    coordinator = PromptDevelopmentCoordinator(
        root, config_path=config_path, context_limits_path=context_limits_path,
        require_environment=False,
    )
    preflight = coordinator._preflight()
    _protocol, lineage = _load_protocol(root, preflight)
    versions: dict[str, Any] = {}
    for version in ("v0", "v1", "v2"):
        assessment_path = next(
            (root / f"{_version_dir(version)}/assessment-n{count:03d}.json"
             for count in (10, 5)
             if (root / f"{_version_dir(version)}/assessment-n{count:03d}.json").is_file()),
            None,
        )
        if assessment_path is None:
            continue
        count = int(assessment_path.stem.removeprefix("assessment-n"))
        assessment = coordinator.recompute(version, count)
        slots: dict[str, Any] = {}
        for model_id in MODEL_IDS:
            model = assessment["models"][model_id]
            report = _read_json_ref(root, model["report_ref"], f"summary/{version}/{model_id}/report")
            slots[model_id] = {
                "trial_count": count,
                "screening_pass": model["screening_pass"],
                "infrastructure_invalid": model["infrastructure_invalid"],
                "repeated_gate_failures": model["repeated_gate_failures"],
                "truncations": model["truncations"],
                "p0": report["metrics"]["p0"],
                "p1": report["metrics"]["p1"],
                "p2": report["metrics"]["p2"],
                "metrics": report["metrics"],
                "schema_after_format_repair_rate": report["metrics"]["schema_after_format_repair_rate"],
                "arch_semantic_first_pass_rate": report["metrics"]["arch_semantic_first_pass_rate"],
                "gates": report["gates"],
                "gate_stages": report["gate_stages"],
                "failure_cooccurrence": report["failure_cooccurrence"],
                "repairs": report["repairs"],
                "usage": report["usage"],
                "model_identity": report["model_identity"],
                "report_ref": dict(model["report_ref"]),
            }
        versions[version] = {
            "trial_count": count,
            "attempt": assessment["attempt"],
            "status": assessment["status"],
            "screening_pass": assessment["screening_pass"],
            "ambiguity": assessment.get("ambiguity"),
            "comparison_tuple": list(_fallback_tuple(assessment)),
            "assessment_ref": _root_ref(root, str(assessment_path.relative_to(root))),
            "slots": slots,
        }
    selection_path = root / "prompt-development/selection.json"
    tie_path = root / "prompt-development/selection-tie.json"
    if selection_path.is_file():
        terminal = _load_json(root, "prompt-development/selection.json", "selection")
    elif tie_path.is_file():
        terminal = _load_json(root, "prompt-development/selection-tie.json", "selection")
    else:
        terminal = {"status": "not-ready"}
    selected = terminal.get("status") == "selected"
    protocol_exceptions: list[dict[str, Any]] = []
    exception_path = root / _SLOT_RETRY_EXCEPTION_PATH
    if exception_path.is_file():
        exception = _load_json(root, _SLOT_RETRY_EXCEPTION_PATH)
        protocol_exceptions.append({
            "ref": _root_ref(root, _SLOT_RETRY_EXCEPTION_PATH),
            "exception": exception.get("exception"),
            "authorization": exception.get("authorization"),
            "model_id": exception.get("model_id"),
            "trial_id": exception.get("trial_id"),
        })
    handoff_ref = _root_ref(root, "prompt-development/handoff.json") if selected and (root / "prompt-development/handoff.json").is_file() else None
    return {
        "schema_version": "2.0",
        "change": "m1-architecture-calibration-redo-through-4a2r",
        "lineage_id": root.name,
        "lineage_root": f"runs/_calibration/s4-architecture/{root.name}/prompt-development",
        "controlled_artifacts": {
            "planning_index": dict(lineage["artifacts"]["planning_index"]),
            "delivery_constraints": dict(lineage["artifacts"]["delivery_constraints"]),
            "schema": dict(lineage["artifacts"]["schema"]),
            "validator": dict(lineage["artifacts"]["validator"]),
        },
        "model_ids": list(MODEL_IDS),
        "versions": versions,
        "terminal": terminal,
        "handoff_ref": handoff_ref,
        "protocol_exceptions": protocol_exceptions,
        "recovery": {
            "status": "not_triggered" if selected else "conditional",
            "provider_calls": 0,
            "recovery_root_created": False,
        },
        "limitations": [
            "This is M1-4a2 development screening evidence, not M1-4a3 N=10 qualification.",
            "No B1-B4, production model, call-shape, budget, formal Run, S4, S5, S6, or production-freeze claim is made.",
        ],
    }


def render_development_report(summary: Mapping[str, Any]) -> str:
    """Render Markdown from a machine summary without hand-entered metrics."""

    lines = [
        "# M1-4a2 development report",
        "",
        f"- Lineage: `{summary['lineage_id']}`",
        f"- Terminal status: `{summary['terminal'].get('status')}`",
        f"- Recovery: `{summary['recovery']['status']}`",
        "",
    ]
    if summary["terminal"].get("status") == "selected":
        lines.extend([
            f"- Selected version: `{summary['terminal']['selected_version']}`",
            f"- Selection reason: `{summary['terminal']['reason']}`",
            f"- M1-4a3 handoff: `{summary['handoff_ref']['path']}`" if summary.get("handoff_ref") else "- M1-4a3 handoff: `missing`",
            "",
        ])
    for version, record in summary.get("versions", {}).items():
        lines.extend([f"## {version}", "", f"- Trials per slot: `{record['trial_count']}`", f"- Screening pass: `{record['screening_pass']}`", ""])
        lines.append("| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for model_id in MODEL_IDS:
            slot = record["slots"][model_id]
            identity = slot["model_identity"]
            lines.append(
                f"| {model_id} | {slot['p0']} | {slot['p1']} | {slot['p2']} | {slot['schema_after_format_repair_rate']} | {slot['arch_semantic_first_pass_rate']} | {slot['usage']['truncated']} | {slot['usage']['cost_usd']} | {json.dumps(identity['model_strings'], ensure_ascii=False, sort_keys=True)} |"
            )
        lines.extend(["", "### Slot diagnostics", ""])
        for model_id in MODEL_IDS:
            slot = record["slots"][model_id]
            lines.append(
                f"- `{model_id}`: infrastructure_invalid=`{slot['infrastructure_invalid']}`, repeated_initial_failures=`{json.dumps(slot['repeated_gate_failures'], sort_keys=True)}`, parameter_support=`{json.dumps(slot['model_identity']['parameter_support'], sort_keys=True)}`"
            )
        lines.extend(["", "### Gate final pass rates", "", "| gate | qwen | claude | deepseek |", "| --- | ---: | ---: | ---: |"])
        for gate in _GATES:
            values = [record["slots"][model_id]["gates"][gate]["rate"] for model_id in MODEL_IDS]
            lines.append(f"| {gate} | {values[0]} | {values[1]} | {values[2]} |")
        lines.append("")
    if summary.get("protocol_exceptions"):
        lines.extend(["## Protocol exceptions", ""])
        for exception in summary["protocol_exceptions"]:
            lines.append(
                f"- `{exception['exception']}`: authorization=`{exception['authorization']}`, slot=`{exception['model_id']}`, trial=`{exception['trial_id']}`, evidence=`{exception['ref']['path']}`"
            )
        lines.append("")
    lines.extend(["## Scope limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def validate_development_report(summary: Mapping[str, Any], markdown: str) -> None:
    if markdown != render_development_report(summary):
        raise PromptDevelopmentEvidenceError("development Markdown does not match the machine summary")


def write_development_report(
    development_root: str | Path,
    *,
    config_path: str | Path,
    context_limits_path: str | Path,
    output_dir: str | Path = "experiments/m1-architecture-calibration-redo-through-4a2r/results",
) -> dict[str, Any]:
    summary = build_development_summary(
        development_root, config_path=config_path, context_limits_path=context_limits_path,
    )
    markdown = render_development_report(summary)
    validate_development_report(summary, markdown)
    destination = Path(output_dir).resolve()
    RunStore._write_atomic_at(destination / "development-summary.json", canonical_json_bytes(summary))
    RunStore._write_atomic_at(destination / "development-report.md", markdown.encode("utf-8"))
    if summary["terminal"].get("status") == "selected":
        recovery_status = {
            "schema_version": "2.0", "change": summary["change"],
            "lineage_id": summary["lineage_id"], "status": "not_triggered",
            "reason": "normal M1-4a2 selection completed; conditional recovery was not entered",
            "recovery_root_created": False, "provider_calls": 0,
        }
        RunStore._write_atomic_at(destination / "recovery-status.json", canonical_json_bytes(recovery_status))
    return summary


def _combine_reports(base: Mapping[str, Any], extension: Mapping[str, Any]) -> dict[str, Any]:
    if base.get("lineage_id") != extension.get("lineage_id") or base.get("prompt_version") != extension.get("prompt_version") or base.get("prompt_sha256") != extension.get("prompt_sha256"):
        raise PromptDevelopmentEvidenceError("base and extension report identity drift")
    if list(base.get("trials", [])) != [f"trial_{index:03d}" for index in range(1, 6)] or list(extension.get("trials", [])) != [f"trial_{index:03d}" for index in range(6, 11)]:
        raise PromptDevelopmentEvidenceError("N10 extension must preserve trials 001-005 and append 006-010")
    result = json.loads(json.dumps(base))
    result["trial_count"] = 10
    result["status"] = "complete" if base.get("status") == extension.get("status") == "complete" else "infrastructure-invalid"
    result["trials"] = list(base["trials"]) + list(extension["trials"])
    result["trial_metrics"] = list(base["trial_metrics"]) + list(extension["trial_metrics"])
    denominator = 10
    metrics = result["metrics"]
    trial_metrics = result["trial_metrics"]
    metrics["schema_first_pass_rate"] = sum(bool(item.get("schema_first_pass")) for item in trial_metrics) / denominator
    metrics["schema_after_format_repair_rate"] = sum(bool(item.get("schema_after_format_repair")) for item in trial_metrics) / denominator
    metrics["arch_raw_first_pass_rate"] = sum(bool(item.get("semantic_first_pass")) and int(item.get("repairs", {}).get("format", 0)) == 0 for item in trial_metrics) / denominator
    metrics["arch_semantic_first_pass_rate"] = sum(bool(item.get("semantic_first_pass")) for item in trial_metrics) / denominator
    metrics["p0"] = sum(bool(item.get("p0")) for item in trial_metrics) / denominator
    metrics["p1"] = sum(bool(item.get("p1")) for item in trial_metrics) / denominator
    metrics["p1_reason"] = None
    metrics["p2_reason"] = {"code": "SEMANTIC_DEPTH_NOT_DECLARED", "message": "p2 is not declared for this batch"}
    for gate in _GATES:
        initial = sum(bool(item["gates"][gate]["initial_passed"]) for item in result["trial_metrics"])
        final = sum(bool(item["gates"][gate]["final_passed"]) for item in result["trial_metrics"])
        result["gates"][gate] = {"passed": final, "denominator": denominator, "rate": final / denominator}
        result["repairs"]["gain"][gate] = {"initial_passed": initial, "final_passed": final, "denominator": denominator, "initial_rate": initial / denominator, "final_rate": final / denominator, "gain": (final - initial) / denominator}
        stage_result: dict[str, Any] = {}
        for stage in ("p0", "p1", "p2"):
            values = [item.get("gate_stages", {}).get(gate, {}).get(stage) for item in trial_metrics]
            if stage == "p2":
                stage_result[stage] = None
                continue
            passed = sum(bool(value) for value in values)
            stage_result[stage] = {"passed": passed, "denominator": denominator, "rate": passed / denominator}
        result["gate_stages"][gate] = stage_result
        result["repairs"]["stage_gain"][gate] = {
            "p0_to_p1": {"improved": sum(not item["gate_stages"][gate]["p0"] and item["gate_stages"][gate]["p1"] for item in trial_metrics), "regressed": sum(item["gate_stages"][gate]["p0"] and not item["gate_stages"][gate]["p1"] for item in trial_metrics), "unchanged": sum(item["gate_stages"][gate]["p0"] == item["gate_stages"][gate]["p1"] for item in trial_metrics), "denominator": denominator, "before_passed": sum(bool(item["gate_stages"][gate]["p0"]) for item in trial_metrics), "after_passed": sum(bool(item["gate_stages"][gate]["p1"]) for item in trial_metrics), "gain": (sum(bool(item["gate_stages"][gate]["p1"]) for item in trial_metrics) - sum(bool(item["gate_stages"][gate]["p0"]) for item in trial_metrics)) / denominator}
            , "p1_to_p2": None,
        }
    result["failure_cooccurrence"] = {left: {right: 0 for right in _GATES} for left in _GATES}
    for item in trial_metrics:
        failed = [gate for gate in _GATES if not item.get("gates", {}).get(gate, {}).get("final_passed")]
        for left in failed:
            for right in failed:
                result["failure_cooccurrence"][left][right] += 1
    result["usage"] = _sum_usage(base.get("usage", {}), extension.get("usage", {}))
    result["repairs"]["format"] = int(base["repairs"].get("format", 0)) + int(extension["repairs"].get("format", 0))
    result["repairs"]["format_usage"] = _sum_usage(base["repairs"].get("format_usage", {}), extension["repairs"].get("format_usage", {}))
    result["repairs"]["semantic"] = {key: int(base["repairs"].get("semantic", {}).get(key, 0)) + int(extension["repairs"].get("semantic", {}).get(key, 0)) for key in ("p1", "p2")}
    result["repairs"]["semantic_usage"] = _sum_usage(base["repairs"].get("semantic_usage", {}), extension["repairs"].get("semantic_usage", {}))
    base_identity = base.get("model_identity", {})
    extension_identity = extension.get("model_identity", {})
    result_identity = json.loads(json.dumps(base_identity))
    result_identity["versions"] = sorted(set(base_identity.get("versions", [])) | set(extension_identity.get("versions", [])))
    result_identity["model_strings"] = sorted(set(base_identity.get("model_strings", [])) | set(extension_identity.get("model_strings", [])))
    result_identity["model_string_shares"] = {
        value: int(base_identity.get("model_string_shares", {}).get(value, 0)) + int(extension_identity.get("model_string_shares", {}).get(value, 0))
        for value in result_identity["model_strings"]
    }
    result["model_identity"] = result_identity
    return result


def _sum_usage(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    finish: dict[str, int] = {}
    for source in (left, right):
        for key, value in source.get("finish_reasons", {}).items():
            finish[key] = finish.get(key, 0) + int(value)
    return {"calls": int(left.get("calls", 0)) + int(right.get("calls", 0)), "tokens_in": int(left.get("tokens_in", 0)) + int(right.get("tokens_in", 0)), "tokens_out": int(left.get("tokens_out", 0)) + int(right.get("tokens_out", 0)), "cost_usd": float(left.get("cost_usd", 0)) + float(right.get("cost_usd", 0)), "latency_ms": int(left.get("latency_ms", 0)) + int(right.get("latency_ms", 0)), "finish_reasons": finish, "truncated": int(left.get("truncated", 0)) + int(right.get("truncated", 0))}


RECOVERY_AUTHORIZATION_ENV = "NEPA_M1_4A2R_AUTHORIZATION"
RECOVERY_CONFIG_ENV = "NEPA_M1_4A2R_CONFIG"
RECOVERY_CONTEXT_LIMITS_ENV = "NEPA_M1_4A2R_CONTEXT_LIMITS"
RECOVERY_VERSIONS = ("r0", "r1", "r2")
_RECOVERY_SCHEMAS = {
    "authorization": "calibration-recovery-authorization.schema.json",
    "provenance": "calibration-recovery-provenance.schema.json",
    "attestation": "calibration-recovery-predecessor-attestation.schema.json",
    "protocol": "calibration-recovery-protocol.schema.json",
    "snapshot": "calibration-recovery-prompt-snapshot.schema.json",
    "revision": "calibration-recovery-revision.schema.json",
    "attempt_declaration": "calibration-recovery-attempt-declaration.schema.json",
    "attempt_outcome": "calibration-recovery-attempt-outcome.schema.json",
    "repair_diff": "calibration-recovery-repair-diff.schema.json",
    "report": "calibration-recovery-report.schema.json",
    "assessment": "calibration-recovery-assessment.schema.json",
    "quality": "calibration-recovery-quality-audit.schema.json",
    "terminal": "calibration-recovery-terminal.schema.json",
    "handoff": "calibration-recovery-handoff.schema.json",
}


def _validate_closed_record(value: Any, schema_key: str, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PromptDevelopmentEvidenceError(f"{label} must be an object")
    errors = sorted(
        Draft202012Validator(load_schema(_RECOVERY_SCHEMAS[schema_key])).iter_errors(value),
        key=lambda item: tuple(item.absolute_path),
    )
    if errors:
        raise PromptDevelopmentEvidenceError(f"invalid {label}: {errors[0].message}")
    strings: set[str] = set()
    def collect(item: Any) -> None:
        if isinstance(item, str):
            strings.add(item)
        elif isinstance(item, Mapping):
            for key, child in item.items():
                collect(key)
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(value)
    forbidden = []
    for name in FIXED_API_KEY_ENVS.values():
        secret = os.environ.get(name)
        if secret and (secret in strings or _sha(secret.encode("utf-8")) in strings):
            forbidden.append(name)
    if forbidden:
        raise PromptDevelopmentEvidenceError(f"{label} contains a Provider credential value")
    return value


def _workspace_source(workspace_root: Path, source: str | Path, label: str) -> tuple[Path, dict[str, str], bytes]:
    root = workspace_root.resolve()
    path = Path(source).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PromptDevelopmentEvidenceError(f"{label} is outside the workspace") from exc
    if not path.is_file():
        raise PromptDevelopmentEvidenceError(f"missing {label}: {relative.as_posix()}")
    data = path.read_bytes()
    return path, {"workspace_path": relative.as_posix(), "sha256": _sha(data)}, data


def verify_recovery_authorization(
    authorization_path: str | Path,
    design_path: str | Path,
    *,
    workspace_root: str | Path = ".",
) -> dict[str, Any]:
    """Fail closed on the independent owner decision and exact design bytes."""

    workspace = Path(workspace_root).resolve()
    _authorization_file, _authorization_source, raw = _workspace_source(workspace, authorization_path, "recovery authorization")
    try:
        authorization = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentEvidenceError("recovery authorization is not valid JSON") from exc
    _validate_closed_record(authorization, "authorization", "recovery authorization")
    _design_file, design_source, design_bytes = _workspace_source(workspace, design_path, "approved design")
    if authorization["approved_design"] != design_source:
        raise PromptDevelopmentEvidenceError("approved design path or SHA-256 does not match the owner authorization")
    design_text = design_bytes.decode("utf-8", errors="strict")
    if "6.4.8.2.1" not in design_text or "M1-4a2r" not in design_text:
        raise PromptDevelopmentEvidenceError("approved design bytes do not contain the current M1-4a2r authority boundary")
    return authorization


def _verify_predecessor_ref(root: Path, ref: Mapping[str, Any], label: str) -> bytes:
    try:
        return _verify_ref(root, ref, label)
    except CalibrationEvidenceError as exc:
        raise PromptDevelopmentEvidenceError(str(exc)) from exc


def attest_predecessor_tie(
    predecessor_root: str | Path,
    *,
    workspace_root: str | Path = ".",
) -> dict[str, Any]:
    """Verify a complete current-contract M1-4a2 selection tie.

    The predecessor is intentionally discovered from its own terminal tie
    artifact.  No historical lineage id, prompt seed hash, model count, or
    fallback tuple is an admission rule here.
    """

    workspace = Path(workspace_root).resolve()
    predecessor = Path(predecessor_root).resolve()
    try:
        predecessor.relative_to(workspace)
    except ValueError as exc:
        raise PromptDevelopmentEvidenceError("predecessor root is outside the workspace") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", predecessor.name):
        raise PromptDevelopmentEvidenceError("recovery predecessor is not a lineage root")
    if (predecessor / "prompt-development/selection.json").exists() or (predecessor / "prompt-development/handoff.json").exists():
        raise PromptDevelopmentEvidenceError("selected predecessor cannot enter post-tie recovery")
    try:
        protocol = _load_json(predecessor, "prompt-development/protocol.json", "protocol")
        lineage = _load_json(predecessor, "lineage.json")
        tie = _load_json(predecessor, "prompt-development/selection-tie.json", "selection")
        _validate_schema_artifact = Draft202012Validator(load_schema("calibration-lineage.schema.json"))
        lineage_errors = sorted(_validate_schema_artifact.iter_errors(lineage), key=lambda item: tuple(item.absolute_path))
        if lineage_errors:
            raise PromptDevelopmentEvidenceError(f"invalid predecessor lineage: {lineage_errors[0].message}")
    except PromptDevelopmentEvidenceError as exc:
        raise PromptDevelopmentEvidenceError("predecessor is not a complete current-contract development lineage") from exc
    if lineage.get("lineage_id") != predecessor.name:
        raise PromptDevelopmentEvidenceError("predecessor lineage identity drift")
    if protocol.get("status") != "initialized" or protocol.get("model_ids") != list(MODEL_IDS) or protocol.get("versions") != ["v0", "v1", "v2"]:
        raise PromptDevelopmentEvidenceError("predecessor protocol is not the complete three-slot V0/V1/V2 protocol")
    if tie.get("status") != "selection-tie" or tie.get("reason") != "PROMPT_SELECTION_TIE":
        raise PromptDevelopmentEvidenceError("predecessor does not contain a terminal prompt-selection tie")
    if tie.get("selected_version") is not None or tie.get("prompt_ref") is not None or tie.get("assessment_ref") is not None:
        raise PromptDevelopmentEvidenceError("predecessor tie contains a selection")
    constraints = json.loads(_verify_root_ref(predecessor, lineage["artifacts"]["delivery_constraints"], "predecessor delivery constraints").decode("utf-8"))
    if not isinstance(constraints.get("layout_convention_id"), str) or not isinstance(constraints.get("layout_convention_sha256"), str):
        raise PromptDevelopmentEvidenceError("predecessor lacks the current layout convention binding")
    schema_bytes = _verify_root_ref(predecessor, lineage["artifacts"]["schema"], "predecessor architecture schema")
    try:
        architecture_schema = json.loads(schema_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentEvidenceError("predecessor architecture schema is invalid") from exc
    if "layout" not in architecture_schema.get("required", []) or "layout" not in architecture_schema.get("properties", {}):
        raise PromptDevelopmentEvidenceError("predecessor architecture schema lacks layout")
    if "arch_15" not in _GATES:
        raise PromptDevelopmentEvidenceError("current architecture gate set is incomplete")
    for group in ("inputs", "artifacts", "components"):
        for name, ref in lineage.get(group, {}).items():
            _verify_predecessor_ref(predecessor, ref, f"predecessor/{group}/{name}")
    tuples: dict[str, list[float]] = {}
    assessment_refs = tie.get("assessment_refs", {})
    if set(assessment_refs) != {"v0", "v1", "v2"}:
        raise PromptDevelopmentEvidenceError("predecessor tie must bind final V0/V1/V2 assessments")
    for version in ("v0", "v1", "v2"):
        assessment_ref = assessment_refs[version]
        assessment = _read_json_ref(predecessor, assessment_ref, f"predecessor/{version}/assessment")
        if assessment.get("lineage_id") != predecessor.name or assessment.get("version") != version or assessment.get("status") != "complete" or assessment.get("screening_pass"):
            raise PromptDevelopmentEvidenceError(f"predecessor {version} is not a complete failing assessment")
        if assessment.get("trial_count") not in {5, 10}:
            raise PromptDevelopmentEvidenceError(f"predecessor {version} has an unsupported final denominator")
        if set(assessment.get("models", {})) != set(MODEL_IDS):
            raise PromptDevelopmentEvidenceError(f"predecessor {version} does not contain all three model slots")
        current_tuple = list(_fallback_tuple(assessment))
        tuples[version] = current_tuple
        for model_id in MODEL_IDS:
            report_ref = assessment.get("models", {}).get(model_id, {}).get("report_ref")
            report_bytes = _verify_predecessor_ref(predecessor, report_ref, f"predecessor/{version}/{model_id}/report")
            report = json.loads(report_bytes.decode("utf-8"))
            if report.get("lineage_id") != predecessor.name or report.get("prompt_version") != version or report.get("model_id") != model_id or report.get("trial_count") != assessment.get("trial_count") or report.get("status") != "complete":
                raise PromptDevelopmentEvidenceError(f"predecessor {version}/{model_id} report binding drift")
            if set(report.get("gates", {})) != set(_GATES) or set(report.get("gate_stages", {})) != set(_GATES):
                raise PromptDevelopmentEvidenceError(f"predecessor {version}/{model_id} lacks the fifteen-gate report")
            model_root = (predecessor / report_ref["path"]).parent
            try:
                recomputed = recompute_calibration_report(model_root)
            except CalibrationEvidenceError as exc:
                raise PromptDevelopmentEvidenceError(f"predecessor {version}/{model_id} report cannot be recomputed") from exc
            if recomputed != report:
                raise PromptDevelopmentEvidenceError(f"predecessor {version}/{model_id} report is not deterministic")
            for trial_id in [f"trial_{index:03d}" for index in range(1, assessment["trial_count"] + 1)]:
                for name in ("request_ref.json", "response_ref.json", "validation.json"):
                    trial_path = model_root / "trials" / trial_id / name
                    if not trial_path.is_file():
                        raise PromptDevelopmentEvidenceError(f"predecessor trial evidence is incomplete: {version}/{model_id}/{trial_id}")
    if tie.get("comparison_tuples") != tuples or len({tuple(value) for value in tuples.values()}) != 1:
        raise PromptDevelopmentEvidenceError("predecessor fallback result is not an exact tie")
    # The graph is the complete immutable predecessor tree.  Recording only
    # report files would leave copied or substituted trial leaves unbound.
    inventory = []
    for path in sorted(predecessor.rglob("*"), key=lambda item: item.relative_to(workspace).as_posix().encode("utf-8")):
        if not path.is_file():
            continue
        inventory.append({"workspace_path": path.relative_to(workspace).as_posix(), "sha256": _sha(path.read_bytes())})
    attestation = {
        "schema_version": "2.0",
        "predecessor_lineage_id": predecessor.name,
        "protocol_status": "initialized",
        "versions": ["v0", "v1", "v2"],
        "model_ids": list(MODEL_IDS),
        "gate_ids": list(_GATES),
        "assessment_refs": {version: dict(assessment_refs[version]) for version in ("v0", "v1", "v2")},
        "comparison_tuples": tuples,
        "outcome": "PROMPT_SELECTION_TIE",
        "selection_absent": True,
        "handoff_absent": True,
        "inventory": inventory,
    }
    attestation["predecessor_graph_sha256"] = _sha(canonical_json_bytes(inventory))
    return _validate_closed_record(attestation, "attestation", "predecessor attestation")


def screen_recovery_report(report: Mapping[str, Any], *, locality_complete: bool, repair_regressions: int) -> bool:
    metrics = report.get("metrics", {})
    return bool(
        report.get("status") == "complete"
        and report.get("trial_count") == 5
        and isinstance(metrics.get("p1"), (int, float))
        and metrics.get("p1") >= 0.80
        and report.get("usage", {}).get("truncated") == 0
        and locality_complete
        and repair_regressions == 0
    )


def build_recovery_quality_audit(
    recovery_root: Path,
    lineage_root: Path,
    version: str,
    attempt: int,
    model_roots: Mapping[str, Path],
    repair_records: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    trials: list[dict[str, Any]] = []
    for model_id in MODEL_IDS:
        model_root = model_roots[model_id]
        for trial_id in [f"trial_{index:03d}" for index in range(1, 6)]:
            validation_record = json.loads((model_root / "trials" / trial_id / "validation.json").read_text(encoding="utf-8"))
            final_attempt = next((item for item in reversed(validation_record["attempts"]) if isinstance(item.get("candidate_ref"), Mapping) and isinstance(item.get("validation_ref"), Mapping)), None)
            if final_attempt is None:
                raise PromptDevelopmentEvidenceError("quality audit requires a final Schema-valid candidate and validation")
            candidate_ref = final_attempt.get("candidate_ref")
            validation_ref = final_attempt.get("validation_ref")
            candidate = json.loads(_verify_ref(model_root, candidate_ref, "quality candidate").decode("utf-8"))
            validation = json.loads(_verify_ref(model_root, validation_ref, "quality validation").decode("utf-8"))
            counts = {wp["id"]: len(wp.get("requirement_responsibilities", [])) for wp in candidate.get("work_packages", [])}
            total = sum(counts.values())
            concentration = (max(counts.values(), default=0) / total) if total else 0.0
            contracts = [item for item in candidate.get("contracts", []) if item.get("ready_gate") == "task"]
            repair = repair_records.get((model_id, trial_id))
            trials.append({
                "model_id": model_id,
                "trial_id": trial_id,
                "candidate_ref": _root_ref(recovery_root, str((model_root / candidate_ref["path"]).relative_to(recovery_root))),
                "validation_ref": _root_ref(recovery_root, str((model_root / validation_ref["path"]).relative_to(recovery_root))),
                "ownership_counts": counts,
                "ownership_concentration": concentration,
                "zero_responsibility_work_packages": sorted(key for key, value in counts.items() if value == 0),
                "consumerless_task_contracts": sorted(str(item.get("id")) for item in contracts if not item.get("consumers")),
                "task_contract_shape_valid": all(item.get("interface_files") and item.get("owner") == item.get("provider") for item in contracts),
                "repair_changed_path_count": len(repair.get("changed_paths", [])) if repair else 0,
                "final_gate_pass": validation.get("verdict") == "pass",
            })
    value = {
        "schema_version": "2.0", "lineage_id": lineage_root.name, "version": version, "attempt": attempt,
        "trials": trials,
        "blind_review": {"status": "unavailable", "reason": "No independent blinded reviewer was declared for recovery."},
    }
    return _validate_closed_record(value, "quality", "recovery quality audit")


def _publish_recovery_json(root: Path, relative: str, value: Any, schema_key: str) -> dict[str, str]:
    _validate_closed_record(value, schema_key, relative)
    try:
        return RunStore(root).publish_immutable_json(relative, value, schema_name=_RECOVERY_SCHEMAS[schema_key]).as_dict()
    except RunStoreError as exc:
        raise PromptDevelopmentEvidenceError(str(exc)) from exc


def _load_recovery_json(root: Path, relative: str, schema_key: str) -> dict[str, Any]:
    try:
        raw = RunStore(root).read_verified_bytes(relative)
        value = json.loads(raw.decode("utf-8"))
    except (RunStoreError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptDevelopmentEvidenceError(f"unable to load recovery record {relative}: {exc}") from exc
    return _validate_closed_record(value, schema_key, relative)


class PromptRecoveryCoordinator:
    """Bounded, fresh-lineage M1-4a2r recovery coordinator."""

    def __init__(
        self,
        recovery_root: str | Path,
        *,
        provider_factory: Any | None = None,
        workspace_root: str | Path = ".",
        prompt_source_path: str | Path = "nepa/agents/prompts/architecture_planner.md",
        require_environment: bool = True,
    ) -> None:
        self.root = Path(recovery_root).resolve()
        if self.root.name != "prompt-recovery" or not re.fullmatch(r"[0-9a-f]{64}", self.root.parent.name):
            raise PromptDevelopmentEvidenceError("recovery root must be <lineage>/prompt-recovery")
        self.lineage_root = self.root.parent
        self.workspace_root = Path(workspace_root).resolve()
        self.prompt_source_path = Path(prompt_source_path).resolve()
        self.provider_factory = provider_factory
        self.require_environment = require_environment

    @classmethod
    def init(
        cls,
        *,
        authorization_path: str | Path,
        design_path: str | Path,
        config_path: str | Path,
        context_limits_path: str | Path,
        spec_path: str | Path,
        target_path: str | Path,
        test_bundle_path: str | Path,
        predecessor_root: str | Path,
        experiment_root: str | Path,
        seed_path: str | Path,
        seed_sha256: str,
        runs_root: str | Path = "runs",
        provider_factory: Any | None = None,
        workspace_root: str | Path = ".",
        prompt_source_path: str | Path = "nepa/agents/prompts/architecture_planner.md",
        require_environment: bool = True,
    ) -> "PromptRecoveryCoordinator":
        workspace = Path(workspace_root).resolve()
        # All authority and external-source checks precede lineage/root creation.
        authorization = verify_recovery_authorization(authorization_path, design_path, workspace_root=workspace)
        attestation = attest_predecessor_tie(predecessor_root, workspace_root=workspace)
        predecessor_identity = {
            "lineage_id": attestation["predecessor_lineage_id"],
            "graph_sha256": attestation["predecessor_graph_sha256"],
        }
        if authorization.get("predecessor") != predecessor_identity:
            raise PromptDevelopmentEvidenceError("recovery authorization does not bind the exact predecessor hash graph")
        preflight = preflight_calibration_config(config_path, context_limits_path, require_environment=require_environment)
        _seed_file, seed_source, seed = _workspace_source(workspace, seed_path, "authorized recovery seed")
        experiment = Path(experiment_root).resolve()
        try:
            seed_file = Path(seed_path).resolve()
            seed_file.relative_to(experiment)
            experiment.relative_to(workspace)
        except ValueError as exc:
            raise PromptDevelopmentEvidenceError("recovery seed is not confined to the declared experiment root") from exc
        if seed_sha256 != seed_source["sha256"]:
            raise PromptDevelopmentEvidenceError("recovery seed SHA-256 does not match its committed bytes")
        if seed_source["workspace_path"].startswith(f"{Path(predecessor_root).resolve().relative_to(workspace).as_posix()}/"):
            raise PromptDevelopmentEvidenceError("recovery seed cannot be copied from the predecessor lineage")
        if any(item["sha256"] == seed_source["sha256"] and "/trials/" in item["workspace_path"] for item in attestation["inventory"]):
            raise PromptDevelopmentEvidenceError("recovery seed cannot be copied from predecessor trial evidence")
        scan_prompt_neutrality(seed)
        prompt_source = Path(prompt_source_path).resolve()
        try:
            prompt_source.relative_to(workspace)
        except ValueError as exc:
            raise PromptDevelopmentEvidenceError("ArchitecturePlanner source is outside the workspace") from exc
        pre_recovery_prompt = _source_prompt_bytes(prompt_source)
        _prompt_file, prompt_source_ref, _prompt_source_bytes = _workspace_source(workspace, prompt_source, "pre-recovery prompt")
        report_sources: list[tuple[dict[str, str], bytes]] = []
        for relative in (
            "results/phase0/phase0-report.md",
            "results/phase1/phase1-report.md",
            "results/phase1/quality-inspection.json",
            "results/phase2/phase2-report.md",
        ):
            candidate = experiment / relative
            if candidate.is_file():
                _path, source, data = _workspace_source(workspace, candidate, "experiment report")
                report_sources.append((source, data))
        declaration = CalibrationBatchDeclaration(
            prompt_version="r0", trial_count=5, semantic_repair_depth=1,
            context_window_tokens=preflight.context_limits,
            spec=spec_path, target_profile=target_path, test_bundle=test_bundle_path,
        )
        recovery_component = recovery_component_bytes()
        driver = ArchitectureCalibrationDriver(
            preflight.config, runs_root=runs_root, provider_factory=provider_factory, prompt_bytes=seed,
            lineage_statistics={"metric_definition": RECOVERY_METRIC_DEFINITION},
            lineage_component_bytes={"statistics": recovery_component},
        )
        prepared, planning, manifest, constraints = driver._prepare(declaration)
        scan_prompt_neutrality(seed, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        targets = declaration.targets(preflight.config)
        lineage_store, lineage = driver._publish_lineage(prepared, planning, manifest, constraints, targets, declaration)
        if lineage["lineage_id"] == attestation["predecessor_lineage_id"]:
            raise PromptDevelopmentEvidenceError("recovery protocol failed to create a fresh lineage")
        root = lineage_store.root / "prompt-recovery"
        store = RunStore(root)
        # Copy small initialization inputs into the new evidence boundary.
        authorization_bytes = canonical_json_bytes(authorization)
        authorization_ref = store.publish_immutable_bytes("provenance/authorization.json", authorization_bytes).as_dict()
        design_file, design_source, design_bytes = _workspace_source(workspace, design_path, "approved design")
        design_ref = store.publish_immutable_bytes("provenance/system_design.md", design_bytes).as_dict()
        seed_ref = store.publish_immutable_bytes("provenance/seed.md", seed).as_dict()
        pre_prompt_ref = store.publish_immutable_bytes("provenance/pre-recovery-prompt.md", pre_recovery_prompt).as_dict()
        config_file, config_source, config_bytes = _workspace_source(workspace, config_path, "recovery config")
        limits_file, limits_source, limits_bytes = _workspace_source(workspace, context_limits_path, "recovery context limits")
        config_ref = store.publish_immutable_bytes("provenance/config.yaml", config_bytes).as_dict()
        limits_ref = store.publish_immutable_bytes("provenance/context-limits.json", limits_bytes).as_dict()
        for kind, source, snapshot in (
            ("authorization", _workspace_source(workspace, authorization_path, "authorization")[1], authorization_ref),
            ("design", design_source, design_ref),
            ("seed", seed_source, seed_ref),
            ("config", config_source, config_ref),
            ("context-limits", limits_source, limits_ref),
            ("pre-recovery-prompt", prompt_source_ref, pre_prompt_ref),
        ):
            _publish_recovery_json(root, f"provenance/{kind}.provenance.json", {"schema_version": "2.0", "kind": kind, "source": source, "snapshot": snapshot}, "provenance")
        for index, (source, data) in enumerate(report_sources, start=1):
            report_ref = store.publish_immutable_bytes(f"provenance/experiment-report-{index:02d}", data).as_dict()
            _publish_recovery_json(root, f"provenance/experiment-report-{index:02d}.provenance.json", {"schema_version": "2.0", "kind": "experiment-report", "source": source, "snapshot": report_ref}, "provenance")
        attestation_ref = _publish_recovery_json(root, "predecessor-attestation.json", attestation, "attestation")
        policy_ref = store.publish_immutable_bytes("components/repair-policy.json", recovery_component).as_dict()
        implementation_ref = store.publish_immutable_bytes("components/recovery-implementation.py", Path(__file__).read_bytes()).as_dict()
        protocol = {
            "schema_version": "2.0", "lineage_id": lineage["lineage_id"], "status": "initialized",
            "authorization_ref": authorization_ref, "design_ref": design_ref,
            "predecessor_attestation_ref": attestation_ref, "seed_ref": seed_ref,
            "predecessor_lineage_id": attestation["predecessor_lineage_id"],
            "predecessor_graph_sha256": attestation["predecessor_graph_sha256"],
            "versions": list(RECOVERY_VERSIONS), "model_ids": list(MODEL_IDS), "trial_count": 5,
            "semantic_depth": 1, "max_tokens": 65536, "api_key_env": dict(FIXED_API_KEY_ENVS),
            "metric_definition": RECOVERY_METRIC_DEFINITION,
            "components": {"repair_policy": policy_ref, "recovery_implementation": implementation_ref},
        }
        _publish_recovery_json(root, "protocol.json", protocol, "protocol")
        # These refs are deliberately kept in a local initialization index, not
        # in later trial/aggregate records.
        store.publish_immutable_json("provenance/runtime-inputs.json", {
            "config_ref": config_ref, "context_limits_ref": limits_ref, "pre_recovery_prompt_ref": pre_prompt_ref,
            "config_source_sha256": config_source["sha256"], "context_source_sha256": limits_source["sha256"],
        })
        # The authorized R0 seed becomes the sole repository prompt only after
        # all immutable initialization parents exist.
        RunStore._write_atomic_at(prompt_source, seed)
        if prompt_source.read_bytes() != seed:
            RunStore._write_atomic_at(prompt_source, pre_recovery_prompt)
            raise PromptDevelopmentEvidenceError("failed to publish and verify the R0 repository prompt")
        coordinator = cls(root, provider_factory=provider_factory, workspace_root=workspace, prompt_source_path=prompt_source, require_environment=require_environment)
        coordinator._publish_prompt_snapshot("r0", seed, planning, constraints)
        return coordinator

    def _runtime_inputs(self) -> tuple[Path, Path, ResolvedConfig, dict[str, int]]:
        value = _load_json(self.root, "provenance/runtime-inputs.json")
        config_ref, limits_ref = value.get("config_ref"), value.get("context_limits_ref")
        config_bytes = _verify_root_ref(self.root, config_ref, "recovery config")
        limits_bytes = _verify_root_ref(self.root, limits_ref, "recovery context limits")
        config_path = self.root / config_ref["path"]
        limits_path = self.root / limits_ref["path"]
        preflight = preflight_calibration_config(config_path, limits_path, require_environment=self.require_environment)
        if config_path.read_bytes() != config_bytes or limits_path.read_bytes() != limits_bytes:
            raise PromptDevelopmentEvidenceError("recovery runtime input snapshot drift")
        return config_path, limits_path, preflight.config, preflight.context_limits

    def _authority_preflight(self) -> dict[str, Any]:
        protocol = _load_recovery_json(self.root, "protocol.json", "protocol")
        if protocol.get("lineage_id") != self.lineage_root.name:
            raise PromptDevelopmentEvidenceError("recovery protocol lineage drift")
        authorization = json.loads(_verify_root_ref(self.root, protocol["authorization_ref"], "authorization snapshot").decode("utf-8"))
        _validate_closed_record(authorization, "authorization", "authorization snapshot")
        design_provenance = _load_recovery_json(self.root, "provenance/design.provenance.json", "provenance")
        design_path = self.workspace_root / design_provenance["source"]["workspace_path"]
        verify_recovery_authorization(self.root / protocol["authorization_ref"]["path"], design_path, workspace_root=self.workspace_root)
        if _sha(design_path.read_bytes()) != protocol["design_ref"]["sha256"]:
            raise PromptDevelopmentEvidenceError("approved design drift blocks further recovery calls and handoff")
        attestation = json.loads(_verify_root_ref(self.root, protocol["predecessor_attestation_ref"], "predecessor attestation").decode("utf-8"))
        _validate_closed_record(attestation, "attestation", "predecessor attestation")
        if attestation.get("predecessor_lineage_id") != protocol.get("predecessor_lineage_id") or attestation.get("predecessor_graph_sha256") != _sha(canonical_json_bytes(attestation.get("inventory", []))):
            raise PromptDevelopmentEvidenceError("predecessor attestation graph binding drift")
        authorization_predecessor = authorization.get("predecessor")
        if authorization_predecessor != {"lineage_id": attestation["predecessor_lineage_id"], "graph_sha256": attestation["predecessor_graph_sha256"]}:
            raise PromptDevelopmentEvidenceError("recovery authorization predecessor binding drift")
        for item in attestation["inventory"]:
            source = self.workspace_root / item["workspace_path"]
            if not source.is_file() or _sha(source.read_bytes()) != item["sha256"]:
                raise PromptDevelopmentEvidenceError("predecessor evidence drift blocks recovery recomputation and handoff")
        for name, ref in protocol["components"].items():
            data = _verify_root_ref(self.root, ref, f"recovery component/{name}")
            if name == "repair_policy" and data != recovery_component_bytes():
                raise PromptDevelopmentEvidenceError("recovery repair policy drift")
            if name == "recovery_implementation" and data != Path(__file__).read_bytes():
                raise PromptDevelopmentEvidenceError("recovery implementation drift requires a new lineage")
        for provenance_path in sorted((self.root / "provenance").glob("*.provenance.json")):
            provenance = _load_recovery_json(self.root, str(provenance_path.relative_to(self.root)), "provenance")
            if provenance["kind"] not in {"authorization", "design", "seed", "experiment-report"}:
                continue
            source = self.workspace_root / provenance["source"]["workspace_path"]
            if not source.is_file() or _sha(source.read_bytes()) != provenance["source"]["sha256"]:
                raise PromptDevelopmentEvidenceError(f"recovery initialization source drift: {provenance['kind']}")
        return protocol

    def _frozen_planning(self) -> tuple[dict[str, Any], dict[str, Any]]:
        lineage = _load_json(self.lineage_root, "lineage.json")
        return _frozen_planning_context(self.lineage_root, lineage)

    def _publish_prompt_snapshot(self, version: str, prompt: bytes, planning: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Any]:
        if version not in RECOVERY_VERSIONS:
            raise PromptDevelopmentError("recovery prompt version must be r0, r1 or r2")
        scan_prompt_neutrality(prompt)
        prompt_ref = _publish_bytes(self.root, f"{version}/prompt.md", prompt)
        schema, example = architecture_draft_contract()
        rendered = _render_architecture_prompt(planning, constraints, None, schema, example, prompt)
        value = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version,
            "prompt_ref": prompt_ref, "prompt_sha256": _sha(prompt), "source_sha256": _sha(prompt),
            "provider_render_sha256": rendered.effective_prompt_sha256,
        }
        _publish_recovery_json(self.root, f"{version}/snapshot.json", value, "snapshot")
        return value

    def _snapshot(self, version: str) -> tuple[dict[str, Any], bytes]:
        value = _load_recovery_json(self.root, f"{version}/snapshot.json", "snapshot")
        prompt = _verify_root_ref(self.root, value["prompt_ref"], f"{version} prompt")
        if value.get("lineage_id") != self.lineage_root.name or value.get("prompt_sha256") != _sha(prompt) or value.get("source_sha256") != _sha(prompt):
            raise PromptDevelopmentEvidenceError("recovery prompt snapshot binding drift")
        planning, constraints = self._frozen_planning()
        schema, example = architecture_draft_contract()
        if value.get("provider_render_sha256") != _render_architecture_prompt(planning, constraints, None, schema, example, prompt).effective_prompt_sha256:
            raise PromptDevelopmentEvidenceError("recovery Provider render identity drift")
        return value, prompt

    def _source_guard(self, prompt: bytes) -> Callable[[], None]:
        def guard() -> None:
            self._authority_preflight()
            if _source_prompt_bytes(self.prompt_source_path) != prompt:
                raise CalibrationDeclarationError("recovery prompt source drifted from the admitted snapshot")
        return guard

    def _attempt_number(self, version: str) -> int:
        path = self.root / version / "attempts"
        outcomes = sorted(path.glob("attempt_*/outcome.json")) if path.is_dir() else []
        if not outcomes:
            return 1
        latest = _load_recovery_json(self.root, str(outcomes[-1].relative_to(self.root)), "attempt_outcome")
        if latest["status"] != "audit-only-infrastructure-invalid":
            raise PromptDevelopmentError("a complete recovery version cannot be rerun")
        return int(latest["attempt"]) + 1

    def _version_admitted(self, version: str) -> None:
        if version == "r0":
            self._snapshot("r0")
            return
        previous = "r0" if version == "r1" else "r1"
        prior = _load_recovery_json(self.root, f"{previous}/assessment.json", "assessment")
        if prior.get("screening_pass"):
            raise PromptDevelopmentError("the first passing recovery version is terminal")
        revision = _load_recovery_json(self.root, f"{version}/revision.json", "revision")
        snapshot, _prompt = self._snapshot(version)
        if revision.get("lineage_id") != self.lineage_root.name or revision.get("new_prompt_sha256") != snapshot.get("prompt_sha256"):
            raise PromptDevelopmentEvidenceError("recovery revision and prompt snapshot drift")

    def _repair_records(self, version: str, attempt: int, model_roots: Mapping[str, Path]) -> dict[tuple[str, str], dict[str, Any]]:
        records: dict[tuple[str, str], dict[str, Any]] = {}
        for model_id, model_root in model_roots.items():
            for trial_id in [f"trial_{index:03d}" for index in range(1, 6)]:
                trial = json.loads((model_root / "trials" / trial_id / "validation.json").read_text(encoding="utf-8"))
                if len(trial.get("attempts", [])) < 2:
                    continue
                before_attempt, after_attempt = trial["attempts"][0], trial["attempts"][1]
                if not isinstance(before_attempt.get("candidate_ref"), Mapping) or not isinstance(before_attempt.get("validation_ref"), Mapping) or not isinstance(after_attempt.get("candidate_ref"), Mapping) or not isinstance(after_attempt.get("validation_ref"), Mapping):
                    continue
                before = json.loads(_verify_ref(model_root, before_attempt["candidate_ref"], "repair before candidate").decode("utf-8"))
                after = json.loads(_verify_ref(model_root, after_attempt["candidate_ref"], "repair after candidate").decode("utf-8"))
                before_validation = json.loads(_verify_ref(model_root, before_attempt["validation_ref"], "repair before validation").decode("utf-8"))
                after_validation = json.loads(_verify_ref(model_root, after_attempt["validation_ref"], "repair after validation").decode("utf-8"))
                record = assess_repair_locality(before, after, list(before_validation.get("issues", [])), before_validation, after_validation)
                _validate_closed_record(record, "repair_diff", "repair diff")
                relative = f"{version}/attempts/attempt_{attempt:03d}/repair-diffs/{model_id}/{trial_id}.json"
                _publish_recovery_json(self.root, relative, record, "repair_diff")
                records[(model_id, trial_id)] = record
        return records

    def _model_recovery_report(
        self,
        version: str,
        attempt: int,
        model_id: str,
        model_root: Path,
        calibration_report: Mapping[str, Any],
        repair_records: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        repaired_successes = [
            metric["trial_id"] for metric in calibration_report.get("trial_metrics", [])
            if metric.get("first_passing_depth") == 1
        ]
        locality_complete = all(
            (model_id, trial_id) in repair_records and repair_records[(model_id, trial_id)].get("locality_pass")
            for trial_id in repaired_successes
        )
        repair_regressions = sum(len(record.get("regressed_gates", [])) for (mid, _), record in repair_records.items() if mid == model_id)
        raw_report_path = model_root / "calibration_report.json"
        value = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version,
            "attempt": attempt, "model_id": model_id, "trial_count": 5,
            "calibration_report_ref": _root_ref(self.root, str(raw_report_path.relative_to(self.root))),
            "schema_after_format_repair_rate": calibration_report.get("metrics", {}).get("schema_after_format_repair_rate"),
            "p0": calibration_report.get("metrics", {}).get("p0"),
            "p1": calibration_report.get("metrics", {}).get("p1"),
            "truncations": calibration_report.get("usage", {}).get("truncated"),
            "infrastructure_valid": calibration_report.get("status") == "complete",
            "repair_regressions": repair_regressions,
            "locality_complete": locality_complete,
            "screening_pass": screen_recovery_report(calibration_report, locality_complete=locality_complete, repair_regressions=repair_regressions),
            "diagnostics": {
                "metrics": calibration_report.get("metrics", {}),
                "gates": calibration_report.get("gates", {}),
                "gate_stages": calibration_report.get("gate_stages", {}),
                "failure_cooccurrence": calibration_report.get("failure_cooccurrence", {}),
                "repairs": calibration_report.get("repairs", {}),
                "usage": calibration_report.get("usage", {}),
                "model_identity": calibration_report.get("model_identity", {}),
                "trial_metrics": calibration_report.get("trial_metrics", []),
            },
        }
        return _validate_closed_record(value, "report", f"{version}/{model_id} recovery report")

    def run_version(self, version: str) -> dict[str, Any]:
        if version not in RECOVERY_VERSIONS:
            raise PromptDevelopmentError("recovery version must be r0, r1 or r2; R3 is forbidden")
        if (self.root / "selection.json").exists() or (self.root / "no-selection.json").exists():
            raise PromptDevelopmentError("terminal recovery forbids later Provider I/O")
        self._authority_preflight()
        self._version_admitted(version)
        snapshot, prompt = self._snapshot(version)
        if _source_prompt_bytes(self.prompt_source_path) != prompt:
            raise PromptDevelopmentEvidenceError("repository prompt does not match the admitted recovery snapshot")
        config_path, limits_path, config, context_limits = self._runtime_inputs()
        planning, constraints = self._frozen_planning()
        scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, config))
        attempt = self._attempt_number(version)
        attempt_base = f"{version}/attempts/attempt_{attempt:03d}"
        trial_ids = [f"trial_{index:03d}" for index in range(1, 6)]
        declaration_record = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version,
            "attempt": attempt, "status": "declared", "prompt_ref": snapshot["prompt_ref"],
            "trial_count": 5, "semantic_depth": 1, "model_ids": list(MODEL_IDS),
            "trial_ids": {model_id: trial_ids for model_id in MODEL_IDS},
        }
        _publish_recovery_json(self.root, f"{attempt_base}/declaration.json", declaration_record, "attempt_declaration")
        lineage = _load_json(self.lineage_root, "lineage.json")
        spec = _verify_root_ref(self.lineage_root, lineage["inputs"]["spec"], "recovery spec")
        target = _verify_root_ref(self.lineage_root, lineage["inputs"]["target"], "recovery target")
        bundle = _verify_root_ref(self.lineage_root, lineage["inputs"]["test_bundle"], "recovery test bundle")
        recovery_component = _verify_root_ref(self.root, _load_recovery_json(self.root, "protocol.json", "protocol")["components"]["repair_policy"], "repair policy")
        driver = ArchitectureCalibrationDriver(
            config, runs_root=self.lineage_root.parents[2], provider_factory=self.provider_factory,
            prompt_bytes=prompt, prompt_source_guard=self._source_guard(prompt),
            lineage_statistics={"metric_definition": RECOVERY_METRIC_DEFINITION},
            lineage_component_bytes={"statistics": recovery_component},
        )
        batch = CalibrationBatchDeclaration(
            prompt_version=version, trial_count=5, semantic_repair_depth=1,
            context_window_tokens=context_limits, spec=spec, target_profile=target, test_bundle=bundle,
            attempt=attempt, root_relative_path=f"prompt-recovery/{attempt_base}",
        )
        model_roots = {model_id: self.root / attempt_base / model_id for model_id in MODEL_IDS}
        try:
            driver.run(batch)
            self._source_guard(prompt)()
            reports = {model_id: recompute_calibration_report(model_roots[model_id], config=config) for model_id in MODEL_IDS}
            if any(report.get("status") != "complete" for report in reports.values()):
                raise PromptDevelopmentEvidenceError("one model batch is infrastructure-invalid")
        except (CalibrationError, PromptDevelopmentEvidenceError) as exc:
            refs = {}
            for model_id, model_root in model_roots.items():
                path = model_root / "calibration_report.json"
                refs[model_id] = _root_ref(self.root, str(path.relative_to(self.root))) if path.is_file() else None
            outcome = {"schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version, "attempt": attempt, "status": "audit-only-infrastructure-invalid", "model_reports": refs}
            _publish_recovery_json(self.root, f"{attempt_base}/outcome.json", outcome, "attempt_outcome")
            raise PromptDevelopmentError("the coherent recovery attempt is audit-only; rerun both complete model batches") from exc
        repair_records = self._repair_records(version, attempt, model_roots)
        wrapper_reports: dict[str, dict[str, Any]] = {}
        wrapper_refs: dict[str, dict[str, str]] = {}
        for model_id in MODEL_IDS:
            wrapper = self._model_recovery_report(version, attempt, model_id, model_roots[model_id], reports[model_id], repair_records)
            wrapper_reports[model_id] = wrapper
            wrapper_refs[model_id] = _publish_recovery_json(self.root, f"{version}/reports/{model_id}.json", wrapper, "report")
        quality = build_recovery_quality_audit(self.root, self.lineage_root, version, attempt, model_roots, repair_records)
        quality_ref = _publish_recovery_json(self.root, f"{version}/quality-audit.json", quality, "quality")
        assessment = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version,
            "attempt": attempt, "status": "complete", "model_reports": wrapper_refs,
            "quality_audit_ref": quality_ref,
            "screening_pass": all(wrapper_reports[model_id]["screening_pass"] for model_id in MODEL_IDS),
        }
        assessment_ref = _publish_recovery_json(self.root, f"{version}/assessment.json", assessment, "assessment")
        outcome = {"schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version, "attempt": attempt, "status": "complete", "model_reports": wrapper_refs}
        _publish_recovery_json(self.root, f"{attempt_base}/outcome.json", outcome, "attempt_outcome")
        if assessment["screening_pass"]:
            self._select(version, snapshot, assessment_ref, quality_ref)
        elif version == "r2":
            self._rollback_no_selection("R2 failed the recovery hard gate")
        return {"assessment": assessment, "next_action": self.next_action()}

    def record_revision(self, version: str, revision_input: Mapping[str, Any], prompt: bytes) -> dict[str, Any]:
        if version not in {"r1", "r2"}:
            raise PromptDevelopmentError("only R1 and R2 are admissible recovery revisions")
        if (self.root / "selection.json").exists() or (self.root / "no-selection.json").exists():
            raise PromptDevelopmentError("terminal recovery forbids prompt edits")
        self._authority_preflight()
        previous = "r0" if version == "r1" else "r1"
        previous_assessment = _load_recovery_json(self.root, f"{previous}/assessment.json", "assessment")
        if previous_assessment.get("screening_pass"):
            raise PromptDevelopmentError("a passing recovery version forbids later revisions")
        if (self.root / f"{version}/revision.json").exists():
            raise PromptDevelopmentError("a recovery version may be revised only once")
        if not isinstance(revision_input, Mapping):
            raise PromptDevelopmentError("revision input must be an object")
        allowed = {"hypothesis", "evidence_refs", "expected_effect", "stopping_conclusion"}
        if set(revision_input) != allowed:
            raise PromptDevelopmentError("revision input has unknown or missing fields")
        hypothesis = revision_input.get("hypothesis")
        stopping = revision_input.get("stopping_conclusion")
        refs = revision_input.get("evidence_refs")
        effect = revision_input.get("expected_effect")
        if not isinstance(hypothesis, str) or not hypothesis.strip() or not isinstance(stopping, str) or not stopping.strip() or not isinstance(refs, list) or not refs or not isinstance(effect, Mapping):
            raise PromptDevelopmentError("revision requires one falsifiable hypothesis, evidence, expected effect and stopping conclusion")
        for ref in refs:
            _verify_root_ref(self.root, ref, "revision evidence")
        if not any(ref.get("path") == f"{previous}/assessment.json" for ref in refs if isinstance(ref, Mapping)):
            raise PromptDevelopmentError("revision evidence must cite the complete immediately preceding assessment")
        if version == "r2":
            prior = _load_recovery_json(self.root, "r1/revision.json", "revision")
            if prior.get("hypothesis", "").strip().casefold() == hypothesis.strip().casefold():
                raise PromptDevelopmentError("R2 requires a hypothesis distinct from R1")
        previous_snapshot, previous_prompt = self._snapshot(previous)
        if prompt == previous_prompt:
            raise PromptDevelopmentError("recovery revision must change shared prompt bytes")
        planning, constraints = self._frozen_planning()
        _config_path, _limits_path, config, _limits = self._runtime_inputs()
        scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, config))
        diff = "".join(difflib.unified_diff(
            previous_prompt.decode("utf-8").splitlines(True), prompt.decode("utf-8").splitlines(True),
            fromfile=previous, tofile=version,
        ))
        value = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version,
            "previous_version": previous, "previous_prompt_sha256": previous_snapshot["prompt_sha256"],
            "new_prompt_sha256": _sha(prompt), "hypothesis": hypothesis.strip(),
            "evidence_refs": [dict(ref) for ref in refs], "unified_diff": diff,
            "expected_effect": dict(effect), "stopping_conclusion": stopping.strip(),
        }
        _validate_closed_record(value, "revision", "recovery revision")
        # The source update is atomic and occurs only after every admission
        # check; failure restores the immediately preceding admitted bytes.
        try:
            RunStore._write_atomic_at(self.prompt_source_path, prompt)
            if self.prompt_source_path.read_bytes() != prompt:
                raise OSError("source verification failed")
            snapshot = self._publish_prompt_snapshot(version, prompt, planning, constraints)
            _publish_recovery_json(self.root, f"{version}/revision.json", value, "revision")
        except BaseException:
            RunStore._write_atomic_at(self.prompt_source_path, previous_prompt)
            raise
        return snapshot

    def _select(self, version: str, snapshot: Mapping[str, Any], assessment_ref: Mapping[str, Any], quality_ref: Mapping[str, Any]) -> dict[str, Any]:
        self._authority_preflight()
        if (self.root / "selection.json").exists():
            return _load_recovery_json(self.root, "selection.json", "terminal")
        prompt = _verify_root_ref(self.root, snapshot["prompt_ref"], "selected prompt")
        if self.prompt_source_path.read_bytes() != prompt:
            raise PromptDevelopmentEvidenceError("repository source must match the selected recovery prompt")
        protocol = _load_recovery_json(self.root, "protocol.json", "protocol")
        value = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name,
            "design_ref": protocol["design_ref"], "predecessor_attestation_ref": protocol["predecessor_attestation_ref"],
            "status": "selected", "selected_version": version, "prompt_ref": snapshot["prompt_ref"],
            "assessment_ref": dict(assessment_ref), "quality_audit_ref": dict(quality_ref),
        }
        selection_ref = _publish_recovery_json(self.root, "selection.json", value, "terminal")
        handoff = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name, "consumer": "m1-4a3",
            "selection_ref": selection_ref, "predecessor_outcome": "PROMPT_SELECTION_TIE",
            "quality_audit_ref": dict(quality_ref),
            "satisfies": {"m1_4a3_admission": True, "n20": False, "p2": False, "b1_b4": False, "production_freeze": False, "owner_signature": False, "formal_run": False, "s5_s6": False},
        }
        _publish_recovery_json(self.root, "m1-4a3-handoff.json", handoff, "handoff")
        return value

    def _rollback_no_selection(self, reason: str) -> dict[str, Any]:
        if (self.root / "selection.json").exists():
            raise PromptDevelopmentError("a selected recovery cannot roll back to no-selection")
        runtime = _load_json(self.root, "provenance/runtime-inputs.json")
        original = _verify_root_ref(self.root, runtime["pre_recovery_prompt_ref"], "pre-recovery prompt")
        RunStore._write_atomic_at(self.prompt_source_path, original)
        if self.prompt_source_path.read_bytes() != original:
            raise PromptDevelopmentEvidenceError("prompt restoration failed; recovery remains nonterminal")
        protocol = _load_recovery_json(self.root, "protocol.json", "protocol")
        value = {
            "schema_version": "2.0", "lineage_id": self.lineage_root.name,
            "design_ref": protocol["design_ref"], "predecessor_attestation_ref": protocol["predecessor_attestation_ref"],
            "status": "no_selection", "restored_prompt_sha256": _sha(original), "reason": reason,
        }
        _publish_recovery_json(self.root, "no-selection.json", value, "terminal")
        return value

    def recompute(self, *, require_complete: bool = False, require_source_match: bool = False) -> dict[str, Any]:
        self._authority_preflight()
        _config_path, _limits_path, config, _context = self._runtime_inputs()
        assessments: dict[str, dict[str, Any]] = {}
        for version in RECOVERY_VERSIONS:
            assessment_path = self.root / version / "assessment.json"
            if not assessment_path.is_file():
                continue
            assessment = _load_recovery_json(self.root, f"{version}/assessment.json", "assessment")
            attempt = int(assessment["attempt"])
            model_roots = {model_id: self.root / version / "attempts" / f"attempt_{attempt:03d}" / model_id for model_id in MODEL_IDS}
            stored_reports: dict[str, dict[str, Any]] = {}
            repair_records = self._repair_records(version, attempt, model_roots)
            for model_id in MODEL_IDS:
                raw = recompute_calibration_report(model_roots[model_id], config=config)
                rebuilt = self._model_recovery_report(version, attempt, model_id, model_roots[model_id], raw, repair_records)
                stored = _load_recovery_json(self.root, assessment["model_reports"][model_id]["path"], "report")
                _verify_root_ref(self.root, assessment["model_reports"][model_id], f"{version}/{model_id} report")
                if rebuilt != stored:
                    raise PromptDevelopmentEvidenceError("recovery model report is not a deterministic projection")
                stored_reports[model_id] = stored
            quality = build_recovery_quality_audit(self.root, self.lineage_root, version, attempt, model_roots, repair_records)
            stored_quality = _load_recovery_json(self.root, assessment["quality_audit_ref"]["path"], "quality")
            _verify_root_ref(self.root, assessment["quality_audit_ref"], f"{version} quality audit")
            if quality != stored_quality:
                raise PromptDevelopmentEvidenceError("recovery quality audit is not deterministic")
            rebuilt_assessment = {
                "schema_version": "2.0", "lineage_id": self.lineage_root.name, "version": version,
                "attempt": attempt, "status": "complete", "model_reports": assessment["model_reports"],
                "quality_audit_ref": assessment["quality_audit_ref"],
                "screening_pass": all(stored_reports[model_id]["screening_pass"] for model_id in MODEL_IDS),
            }
            if rebuilt_assessment != assessment:
                raise PromptDevelopmentEvidenceError("recovery assessment is not deterministic")
            assessments[version] = assessment
        passing = [version for version in RECOVERY_VERSIONS if assessments.get(version, {}).get("screening_pass")]
        if len(passing) > 1 or (passing and passing[0] != next(version for version in RECOVERY_VERSIONS if version in assessments and assessments[version].get("screening_pass"))):
            raise PromptDevelopmentEvidenceError("recovery has more than one selectable version")
        terminal: dict[str, Any] | None = None
        if (self.root / "selection.json").is_file():
            terminal = _load_recovery_json(self.root, "selection.json", "terminal")
            handoff = _load_recovery_json(self.root, "m1-4a3-handoff.json", "handoff")
            if not passing or terminal.get("selected_version") != passing[0] or handoff.get("selection_ref") != _root_ref(self.root, "selection.json"):
                raise PromptDevelopmentEvidenceError("terminal recovery selection/handoff is not the first passing version")
        elif (self.root / "no-selection.json").is_file():
            terminal = _load_recovery_json(self.root, "no-selection.json", "terminal")
            if passing:
                raise PromptDevelopmentEvidenceError("no-selection conflicts with passing recovery evidence")
        if require_complete and (terminal is None or terminal.get("status") != "selected"):
            raise PromptDevelopmentEvidenceError("complete recovery requires one passing selection and M1-4a3 handoff")
        if require_source_match and terminal is not None:
            if terminal["status"] == "selected":
                expected = _verify_root_ref(self.root, terminal["prompt_ref"], "terminal selected prompt")
            else:
                runtime = _load_json(self.root, "provenance/runtime-inputs.json")
                expected = _verify_root_ref(self.root, runtime["pre_recovery_prompt_ref"], "terminal restored prompt")
            if self.prompt_source_path.read_bytes() != expected:
                raise PromptDevelopmentEvidenceError("repository prompt does not match the terminal recovery state")
        return {"lineage_id": self.lineage_root.name, "assessments": assessments, "terminal": terminal, "next_action": self.next_action()}

    def next_action(self) -> dict[str, Any]:
        if (self.root / "selection.json").is_file():
            return {"action": "terminal-selection"}
        if (self.root / "no-selection.json").is_file():
            return {"action": "terminal-no-selection"}
        for version in RECOVERY_VERSIONS:
            assessment_path = self.root / version / "assessment.json"
            if not assessment_path.is_file():
                if version == "r0" or (self.root / version / "revision.json").is_file():
                    return {"action": "run-version", "version": version, "attempt": self._attempt_number(version)}
                return {"action": "record-revision", "version": version}
            assessment = _load_recovery_json(self.root, f"{version}/assessment.json", "assessment")
            if assessment.get("screening_pass"):
                return {"action": "recompute-selection", "version": version}
        return {"action": "rollback-no-selection"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nepa.calibration.s4_prompt_development")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--config", required=True)
    init.add_argument("--context-limits", required=True)
    init.add_argument("--runs-root", default="runs")
    init.add_argument("--spec", default="gold_file/specIR.json")
    init.add_argument("--target", default=None)
    init.add_argument("--test-bundle", default=None)
    for name in ("run-version", "record-revision", "expand", "recompute"):
        command = sub.add_parser(name)
        command.add_argument("--development-root", required=True)
        command.add_argument("--config", default=None)
        command.add_argument("--context-limits", default=None)
        if name != "record-revision":
            command.add_argument("--version", default="v0")
        if name == "record-revision":
            command.add_argument("--version", required=True)
            command.add_argument("--input", default=None)
            command.add_argument("--hypothesis", default=None)
            command.add_argument("--prompt", default=None)
        if name == "recompute":
            command.add_argument("--count", type=int, default=5)
            command.add_argument("--require-complete", action="store_true")
            command.add_argument("--require-source-match", action="store_true")
    slot_retry = sub.add_parser("retry-extension-slot")
    slot_retry.add_argument("--development-root", required=True)
    slot_retry.add_argument("--config", default=None)
    slot_retry.add_argument("--context-limits", default=None)
    slot_retry.add_argument("--version", default="v2")
    slot_retry.add_argument("--model-id", default="claude")
    slot_retry.add_argument("--trial-id", default="trial_010")
    select = sub.add_parser("select")
    select.add_argument("--development-root", required=True)
    select.add_argument("--config", default=None)
    select.add_argument("--context-limits", default=None)
    select.add_argument("--version", default="v2")
    recovery_init = sub.add_parser("recovery-init")
    recovery_init.add_argument("--authorization", required=True)
    recovery_init.add_argument("--design", required=True)
    recovery_init.add_argument("--config", required=True)
    recovery_init.add_argument("--context-limits", required=True)
    recovery_init.add_argument("--spec", required=True)
    recovery_init.add_argument("--target", required=True)
    recovery_init.add_argument("--test-bundle", required=True)
    recovery_init.add_argument("--predecessor-root", required=True)
    recovery_init.add_argument("--experiment-root", required=True)
    recovery_init.add_argument("--seed", required=True)
    recovery_init.add_argument("--seed-sha256", required=True)
    recovery_init.add_argument("--runs-root", default="runs")
    recovery_run = sub.add_parser("recovery-run-version")
    recovery_run.add_argument("--recovery-root", required=True)
    recovery_run.add_argument("--version", required=True)
    recovery_revision = sub.add_parser("recovery-record-revision")
    recovery_revision.add_argument("--recovery-root", required=True)
    recovery_revision.add_argument("--version", required=True)
    recovery_revision.add_argument("--input", required=True)
    recovery_recompute = sub.add_parser("recovery-recompute")
    recovery_recompute.add_argument("--recovery-root", required=True)
    recovery_recompute.add_argument("--require-complete", action="store_true")
    recovery_recompute.add_argument("--require-source-match", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("--development-root", required=True)
    report.add_argument("--config", required=True)
    report.add_argument("--context-limits", required=True)
    report.add_argument("--output-dir", default="experiments/m1-architecture-calibration-redo-through-4a2r/results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "recovery-init":
        coordinator = PromptRecoveryCoordinator.init(
            authorization_path=args.authorization, design_path=args.design,
            config_path=args.config, context_limits_path=args.context_limits,
            spec_path=args.spec, target_path=args.target, test_bundle_path=args.test_bundle,
            predecessor_root=args.predecessor_root, experiment_root=args.experiment_root,
            seed_path=args.seed, seed_sha256=args.seed_sha256, runs_root=args.runs_root,
            require_environment=True,
        )
        value = {"lineage_root": str(coordinator.lineage_root), "recovery_root": str(coordinator.root), "next_action": coordinator.next_action()}
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command.startswith("recovery-"):
        coordinator = PromptRecoveryCoordinator(args.recovery_root, require_environment=True)
        if args.command == "recovery-run-version":
            value = coordinator.run_version(args.version)
        elif args.command == "recovery-record-revision":
            record = json.loads(Path(args.input).read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not isinstance(record.get("prompt_path"), str):
                raise PromptDevelopmentError("recovery revision input requires prompt_path")
            prompt = Path(record.pop("prompt_path")).read_bytes()
            value = coordinator.record_revision(args.version, record, prompt)
        else:
            value = coordinator.recompute(require_complete=args.require_complete, require_source_match=args.require_source_match)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "report":
        value = write_development_report(
            args.development_root, config_path=args.config,
            context_limits_path=args.context_limits, output_dir=args.output_dir,
        )
        print(json.dumps({"lineage_id": value["lineage_id"], "output_dir": str(Path(args.output_dir).resolve())}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "init":
        coordinator = PromptDevelopmentCoordinator.init(config_path=args.config, context_limits_path=args.context_limits, runs_root=args.runs_root, require_environment=True, spec_path=args.spec, target_path=args.target, test_bundle_path=args.test_bundle)
        print(json.dumps({"lineage_root": str(coordinator.root)}, sort_keys=True))
        return 0
    config_path = args.config or os.environ.get(CONFIG_ENV)
    context_limits_path = args.context_limits or os.environ.get(CONTEXT_LIMITS_ENV)
    if not config_path or not context_limits_path:
        raise PromptDevelopmentConfigError(f"{CONFIG_ENV} and {CONTEXT_LIMITS_ENV} must name explicit files for this operation")
    coordinator = PromptDevelopmentCoordinator(args.development_root, config_path=config_path, context_limits_path=context_limits_path, require_environment=True)
    if args.command == "run-version":
        value = coordinator.run_version(args.version)
    elif args.command == "record-revision":
        if args.input:
            record = json.loads(Path(args.input).read_text(encoding="utf-8"))
            hypothesis = record["hypothesis"]
            evidence_refs = record["evidence_refs"]
            prompt_path = record.get("prompt_path")
            prompt_bytes = Path(prompt_path).read_bytes() if prompt_path else None
        else:
            if not args.hypothesis or not args.prompt:
                raise PromptDevelopmentError("record-revision requires --input or --hypothesis and --prompt")
            hypothesis = args.hypothesis
            evidence_refs = [_root_ref(coordinator.root, f"{_version_dir('v0')}/assessment-n005.json")]
            prompt_bytes = Path(args.prompt).read_bytes()
        value = coordinator.record_revision(args.version, hypothesis=hypothesis, evidence_refs=evidence_refs, expected_gates=record.get("expected_gates") if args.input else None, expected_metrics=record.get("expected_metrics") if args.input else None, prompt_bytes=prompt_bytes)
    elif args.command == "expand":
        value = coordinator.expand(args.version)
    elif args.command == "retry-extension-slot":
        value = coordinator.retry_extension_slot(args.version, model_id=args.model_id, trial_id=args.trial_id)
    elif args.command == "select":
        value = coordinator.select(
            args.version,
            reason="unique fixed fallback winner after authorized single-slot Claude retry exception",
        )
    else:
        value = coordinator.recompute(args.version, args.count, require_complete=args.require_complete, require_source_match=args.require_source_match)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONFIG_ENV", "CONTEXT_LIMITS_ENV", "RECOVERY_AUTHORIZATION_ENV", "RECOVERY_CONFIG_ENV", "RECOVERY_CONTEXT_LIMITS_ENV",
    "CalibrationPreflight", "PromptDevelopmentConfigError", "PromptDevelopmentCoordinator", "PromptRecoveryCoordinator",
    "PromptDevelopmentError", "PromptDevelopmentEvidenceError", "PromptSelectionTie", "preflight_calibration_config",
    "scan_prompt_neutrality", "verify_recovery_authorization", "attest_predecessor_tie", "screen_recovery_report",
    "build_development_summary", "render_development_report", "validate_development_report", "write_development_report",
]
