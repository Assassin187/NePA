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
from ..speclib.planning import prepare_architecture_inputs
from . import s4_architecture as _architecture
from .s4_architecture import (
    FIXED_API_KEY_ENVS,
    MODEL_IDS,
    METRIC_DEFINITION,
    RECOVERY_METRIC_DEFINITION,
    REPAIR_IMPACT_POLICY,
    REPAIR_IMPACT_POLICY_VERSION,
    ArchitectureCalibrationDriver,
    ARCHITECTURE_PROMPT_PATHS,
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
_DEV_VERSIONS = ("v0", "v1", "v2")
_MODEL_SLOT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_GATES = tuple(f"arch_{index:02d}" for index in range(1, 16))
_METRIC_NAMES = ("schema_after_format_repair_rate", "p1", "p2", "arch_semantic_first_pass_rate")
CONFIG_ENV = "NEPA_M1_4A2_CONFIG"
CONTEXT_LIMITS_ENV = "NEPA_M1_4A2_CONTEXT_LIMITS"
_SLOT_RETRY_EXCEPTION_PATH = "v2/extensions/n010/slot-retry-001/exception.json"
_EXECUTION_AMENDMENT_PATH = "prompt-development/execution-amendment-005.json"
_SCHEMA_NAMES = {
    "protocol": "calibration-baseline-protocol.schema.json",
    "version": "calibration-baseline-version.schema.json",
    "snapshot": "calibration-baseline-snapshot.schema.json",
    "revision": "calibration-baseline-revision.schema.json",
    "attempt_declaration": "calibration-baseline-attempt-declaration.schema.json",
    "attempt_outcome": "calibration-baseline-attempt-outcome.schema.json",
    "extension": "calibration-development-extension.schema.json",
    "assessment": "calibration-baseline-assessment.schema.json",
    "outcome": "calibration-baseline-outcome.schema.json",
    "selection": "calibration-baseline-selection.schema.json",
    "handoff": "calibration-baseline-handoff.schema.json",
    "owner_approval": "calibration-baseline-owner-approval.schema.json",
    "report": "calibration-report.schema.json",
}
_LEGACY_SCHEMA_NAMES = {
    "protocol": "calibration-development-protocol.schema.json",
    "version": "calibration-prompt-version.schema.json",
    "snapshot": "calibration-prompt-snapshot.schema.json",
    "revision": "calibration-prompt-revision.schema.json",
    "attempt_declaration": "calibration-attempt-declaration.schema.json",
    "attempt_outcome": "calibration-attempt-outcome.schema.json",
    "assessment": "calibration-development-assessment.schema.json",
    "outcome": "calibration-development-outcome.schema.json",
    "selection": "calibration-development-selection.schema.json",
    "handoff": "calibration-development-handoff.schema.json",
}
_BUNDLE_SCHEMA_NAMES = {
    "protocol": "calibration-development-protocol-bundle.schema.json",
    "version": "calibration-prompt-version-bundle.schema.json",
    "snapshot": "calibration-prompt-snapshot-bundle.schema.json",
    "revision": "calibration-prompt-revision-bundle.schema.json",
    "attempt_declaration": "calibration-attempt-declaration-bundle.schema.json",
    "attempt_outcome": "calibration-attempt-outcome.schema.json",
    "assessment": "calibration-development-assessment.schema.json",
    "outcome": "calibration-development-outcome.schema.json",
    "selection": "calibration-development-selection-bundle.schema.json",
    "handoff": "calibration-development-handoff-bundle.schema.json",
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


def _execution_amendment_authorized(root: Path) -> bool:
    """Accept only the user-authorized executor amendment for this lineage."""

    path = root / _EXECUTION_AMENDMENT_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        lineage = json.loads((root / "lineage.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or not isinstance(lineage, dict):
        return False
    current_statistics = _sha(_default_components("patch")["statistics"])
    return (
        value.get("schema_version") == "4.0"
        and value.get("lineage_id") == root.name
        and value.get("status") == "admitted"
        and value.get("authorization") == "explicit_user_authorization"
        and value.get("scope") == "trial-local-retry"
        and value.get("versions") == ["v1"]
        and value.get("recorded_statistics_sha256") == lineage.get("components", {}).get("statistics", {}).get("sha256")
        and value.get("current_statistics_sha256") == current_statistics
        and value.get("implementation_sha256") == _sha((Path(__file__).resolve().parent / "s4_architecture.py").read_bytes())
    )


def _recompute_lineage_report(root: Path, model_root: str | Path, *, config: ResolvedConfig | None = None) -> dict[str, Any]:
    if not (_slot_retry_exception_authorized(root) or _execution_amendment_authorized(root)):
        return recompute_calibration_report(model_root, config=config)
    recorded_components = {
        name: _verify_root_ref(root, ref, f"lineage/components/{name}")
        for name, ref in _load_json(root, "lineage.json").get("components", {}).items()
    }
    original_components = _architecture._default_components
    _architecture._default_components = lambda *args, **kwargs: dict(recorded_components)
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
        version = value.get("schema_version") if isinstance(value, Mapping) else None
        schema_name = (_LEGACY_SCHEMA_NAMES if version == "2.0" else _BUNDLE_SCHEMA_NAMES if version == "3.0" else _SCHEMA_NAMES)[schema_key]
        ref = store.publish_immutable_json(relative, value, schema_name=schema_name)
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
        version = value.get("schema_version")
        schema_name = (_LEGACY_SCHEMA_NAMES if version == "2.0" else _BUNDLE_SCHEMA_NAMES if version == "3.0" else _SCHEMA_NAMES)[schema_key]
        errors = sorted(Draft202012Validator(load_schema(schema_name)).iter_errors(value), key=lambda item: tuple(item.absolute_path))
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


def _source_prompt_bundle(
    initial_path: Path | None = None,
    repair_path: Path | None = None,
) -> dict[str, bytes]:
    paths = {"initial": initial_path, "repair": repair_path}
    bundle: dict[str, bytes] = {}
    for phase, path in paths.items():
        if path is not None:
            try:
                bundle[phase] = path.read_bytes()
            except OSError as exc:
                raise PromptDevelopmentEvidenceError(f"unable to read ArchitecturePlanner {phase} prompt source: {exc}") from exc
        else:
            definition = get_role("architecture_planner").model_copy(update={"template_path": ARCHITECTURE_PROMPT_PATHS[phase]})
            bundle[phase] = PromptRenderer._load_template(definition).raw
    return bundle


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
            for model_id in sorted(self.config.calibration_models)
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
    model_slots = tuple(sorted(config.calibration_models))
    if len(model_slots) != 1 or _MODEL_SLOT_RE.fullmatch(model_slots[0]) is None:
        raise PromptDevelopmentConfigError("calibration_models must contain exactly one valid logical model slot")
    if not isinstance(raw_limits, dict) or set(raw_limits) != set(model_slots):
        raise PromptDevelopmentConfigError("context limits must contain exactly the configured logical model slot")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in raw_limits.values()):
        raise PromptDevelopmentConfigError("context limits must be positive integers")
    required_envs: list[str] = []
    for model_id in model_slots:
        model = config.calibration_models[model_id]
        provider = config.providers.get(model.provider)
        if provider is None:
            raise PromptDevelopmentConfigError(f"missing provider for {model_id}")
        if not provider.api_key_env:
            raise PromptDevelopmentConfigError(f"missing api_key_env for {model_id}")
        required_envs.append(provider.api_key_env)
        if f"{model.provider}/{model.model}" not in config.pricing.models:
            raise PromptDevelopmentConfigError(f"missing pricing for {model_id}")
        if model.temperature < 0 or model.max_tokens <= 0:
            raise PromptDevelopmentConfigError(f"invalid request parameters for {model_id}")
    if require_environment:
        missing = [name for name in required_envs if not os.environ.get(name)]
        if missing:
            raise PromptDevelopmentConfigError("missing required environment variable: " + ", ".join(missing))
    snapshot = public_config_snapshot(config)
    context_bytes = canonical_json_bytes({model_id: int(raw_limits[model_id]) for model_id in model_slots})
    return CalibrationPreflight(
        config_path=config_file,
        context_limits_path=limits_file,
        config=config,
        context_limits={model_id: int(raw_limits[model_id]) for model_id in model_slots},
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
    execution_amendment_authorized = _execution_amendment_authorized(root)
    if (
        statistics.get("metric_definition") != METRIC_DEFINITION
        or (
            statistics.get("implementation_sha256")
            != _sha((Path(__file__).resolve().parent / "s4_architecture.py").read_bytes())
            and not (exception_authorized or execution_amendment_authorized)
        )
    ):
        raise PromptDevelopmentEvidenceError("lineage metric-definition implementation drift")
    model_slots = tuple(sorted(preflight.config.calibration_models))
    expected_api_keys = {
        model_id: preflight.config.providers[preflight.config.calibration_models[model_id].provider].api_key_env
        for model_id in model_slots
    }
    if lineage.get("calibration", {}).get("api_key_env") != expected_api_keys:
        raise PromptDevelopmentEvidenceError("lineage API-key mapping drift")
    expected_controls = {
        model_id: {
            "provider": preflight.config.calibration_models[model_id].provider,
            "temperature": preflight.config.calibration_models[model_id].temperature,
            "max_tokens": preflight.config.calibration_models[model_id].max_tokens,
            "context_window_tokens": preflight.context_limits[model_id],
        }
        for model_id in model_slots
    }
    if lineage.get("slot_controls") != expected_controls:
        raise PromptDevelopmentEvidenceError("resolved slot control projection drift")
    if set(lineage.get("models", {})) != set(model_slots):
        raise PromptDevelopmentEvidenceError("lineage model observations are incomplete")
    expected_provider_names = {preflight.config.calibration_models[model_id].provider for model_id in model_slots}
    if set(lineage.get("providers", {})) != expected_provider_names or set(lineage.get("pricing", {})) != set(model_slots):
        raise PromptDevelopmentEvidenceError("lineage provider or pricing projection drift")
    for model_id in model_slots:
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
    current = _default_components(lineage.get("repair_mode", "full_draft"))
    if set(current) != set(lineage.get("components", {})):
        raise PromptDevelopmentEvidenceError("controlled component set drift")
    for name, data in current.items():
        if lineage["components"][name].get("sha256") != _sha(data) and not (
            (exception_authorized or execution_amendment_authorized) and name == "statistics"
        ):
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
    model_slot = protocol.get("model_slot")
    if not isinstance(model_slot, str) or _MODEL_SLOT_RE.fullmatch(model_slot) is None or protocol.get("versions") != list(_DEV_VERSIONS):
        raise PromptDevelopmentEvidenceError("protocol model or version set drift")
    if protocol.get("semantic_depth") != 2 or protocol.get("base_trial_count") != 3 or protocol.get("max_revisions") != 2 or protocol.get("initial_trial_ceiling") != 9 or protocol.get("repair_mode") != "patch":
        raise PromptDevelopmentEvidenceError("protocol batch controls drift")
    if protocol.get("schema_version") != "4.0" or protocol.get("bundle_unit") != "initial-repair" or protocol.get("stages") != ["initial", "repair"]:
        raise PromptDevelopmentEvidenceError("protocol prompt bundle controls drift")
    if protocol.get("metric_definition") != METRIC_DEFINITION or not isinstance(protocol.get("api_key_env"), str):
        raise PromptDevelopmentEvidenceError("protocol controlled metric or key mapping drift")
    if protocol.get("screening") != {
        "trial_count": 3,
        "p2_required_passes": 2,
        "max_effective_repairs": 2,
    }:
        raise PromptDevelopmentEvidenceError("protocol screening contract drift")
    if protocol.get("components") != lineage.get("components"):
        raise PromptDevelopmentEvidenceError("protocol component binding drift")
    if preflight is not None:
        if tuple(preflight.config.calibration_models) != (model_slot,):
            raise PromptDevelopmentEvidenceError("protocol model slot and current configuration differ")
        provider_name = preflight.config.calibration_models[model_slot].provider
        if protocol.get("api_key_env") != preflight.config.providers[provider_name].api_key_env:
            raise PromptDevelopmentEvidenceError("protocol API-key environment name drift")
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
    if value.get("schema_version") == "2.0":
        prompt_ref = value.get("prompt_ref")
        expected_prompt_path = f"{_version_dir(version)}/prompt.md"
        if not isinstance(prompt_ref, Mapping) or prompt_ref.get("path") != expected_prompt_path:
            raise PromptDevelopmentEvidenceError("historical prompt version is not bound to its immutable snapshot bytes")
        prompt = _verify_root_ref(root, prompt_ref, "historical prompt version bytes")
        if value.get("prompt_sha256") != _sha(prompt) or value.get("source_prompt_sha256") != _sha(prompt):
            raise PromptDevelopmentEvidenceError("historical prompt version hash binding drift")
        return value
    bundle_ref = value.get("bundle_ref")
    expected_bundle_path = f"{_version_dir(version)}/snapshot.json"
    if not isinstance(bundle_ref, Mapping) or bundle_ref.get("path") != expected_bundle_path:
        raise PromptDevelopmentEvidenceError("prompt version is not bound to its immutable bundle snapshot")
    _verify_root_ref(root, bundle_ref, "prompt version bundle")
    snapshot = _load_json(root, expected_bundle_path, "snapshot")
    if snapshot.get("lineage_id") != root.name or snapshot.get("version") != version:
        raise PromptDevelopmentEvidenceError("prompt bundle snapshot lineage or version binding drift")
    for phase in ARCHITECTURE_PROMPT_PATHS:
        ref = snapshot.get(f"{phase}_ref")
        if not isinstance(ref, Mapping) or ref.get("path") != f"{_version_dir(version)}/{phase}.md":
            raise PromptDevelopmentEvidenceError(f"prompt bundle snapshot has no {phase} source")
        _verify_root_ref(root, ref, f"prompt bundle {phase} source")
    return value


def _load_bundle(root: Path, version: str) -> dict[str, bytes]:
    snapshot = _load_json(root, f"{_version_dir(version)}/snapshot.json", "snapshot")
    bundle: dict[str, bytes] = {}
    for phase in ARCHITECTURE_PROMPT_PATHS:
        ref = snapshot.get(f"{phase}_ref")
        bundle[phase] = _verify_root_ref(root, ref, f"{version}/{phase} prompt")
    return bundle


def _assessment_path_for_attempt(version: str, attempt: int, count: int = 3) -> str:
    if count != 3 or attempt < 1:
        raise PromptDevelopmentEvidenceError("current development assessments require N=3 and a positive attempt")
    prefix = f"{_version_dir(version)}/"
    if attempt > 1:
        prefix += f"attempts/attempt_{attempt:03d}/"
    return f"{prefix}assessment-n{count:03d}.json"


def _assessment_records(root: Path, version: str, count: int) -> list[tuple[int, str, dict[str, Any]]]:
    """Return all immutable assessment leaves, ordered by attempt."""

    records: list[tuple[int, str, dict[str, Any]]] = []
    candidates: list[tuple[int, Path]] = [(1, root / _assessment_path_for_attempt(version, 1, count))]
    attempts_root = root / _version_dir(version) / "attempts"
    if attempts_root.is_dir():
        for path in attempts_root.glob("attempt_*/assessment-n003.json"):
            match = re.fullmatch(r"attempt_(\d{3})", path.parent.name)
            if match:
                candidates.append((int(match.group(1)), path))
    seen_attempts: set[int] = set()
    for expected_attempt, path in candidates:
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        value = _load_json(root, relative, "assessment")
        if expected_attempt in seen_attempts or value.get("attempt") != expected_attempt:
            raise PromptDevelopmentEvidenceError("assessment attempt binding drift")
        if value.get("lineage_id") != root.name or value.get("version") != version or value.get("trial_count") != count:
            raise PromptDevelopmentEvidenceError("assessment lineage, version, or trial-count binding drift")
        seen_attempts.add(expected_attempt)
        records.append((expected_attempt, relative, value))
    return sorted(records, key=lambda item: item[0])


def _assessment_record(root: Path, version: str, count: int) -> tuple[int, str, dict[str, Any]]:
    records = _assessment_records(root, version, count)
    if not records:
        raise PromptDevelopmentEvidenceError(f"missing assessment for {version}")
    return records[-1]


def _load_assessment(root: Path, version: str, count: int) -> dict[str, Any]:
    return _assessment_record(root, version, count)[2]


def _expected_report_path(version: str, attempt: int, count: int, model_id: str) -> str:
    if count != 3:
        raise PromptDevelopmentEvidenceError("current development reports require exactly N=3")
    prefix = f"{version}/"
    if attempt > 1:
        prefix += f"attempt_{attempt:03d}/"
    return f"{prefix}{model_id}/calibration_report.json"


def _is_bound_report_path(
    version: str,
    attempt: int,
    count: int,
    model_id: str,
    path: Any,
) -> bool:
    if not isinstance(path, str):
        return False
    if path == _expected_report_path(version, attempt, count, model_id):
        return True
    return False


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
    p2 = metrics.get("p2")
    semantic = metrics.get("arch_semantic_first_pass_rate")
    schema_rate = metrics.get("schema_after_format_repair_rate")
    infrastructure = report.get("status") != "complete"
    p2_passes = sum(bool(item.get("p2")) for item in trial_metrics)
    unavailable_trials = sum(item.get("terminal") == "infrastructure-invalid" for item in trial_metrics)
    passed = len(trial_metrics) == 3 and p2_passes >= 2
    return {
        "p1": p1 if isinstance(p1, (int, float)) else 0.0,
        "p2": p2 if isinstance(p2, (int, float)) else 0.0,
        "schema_after_format_repair_rate": schema_rate if isinstance(schema_rate, (int, float)) else 0.0,
        "arch_semantic_first_pass_rate": semantic if isinstance(semantic, (int, float)) else 0.0,
        "truncations": int(usage.get("truncated", 0)),
        "infrastructure_invalid": infrastructure,
        "p2_passes": p2_passes,
        "unavailable_trials": unavailable_trials,
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
        p2_rate = sum(bool(item.get("p2")) for item in remaining) / len(remaining)
        truncated = sum(int(item.get("usage", {}).get("truncated", 0)) for item in remaining)
        candidate_pass = schema_rate == 1.0 and p2_rate >= 0.5 and truncated == 0
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
) -> dict[str, Any]:
    if trial_count != 3:
        raise PromptDevelopmentEvidenceError("M1-4a2 development assessments require exactly N=3 per slot")
    expected_trials = [f"trial_{index:03d}" for index in range(1, trial_count + 1)]
    models: dict[str, Any] = {}
    model_ids = tuple(sorted(reports))
    if len(model_ids) != 1 or set(report_refs) != set(model_ids):
        raise PromptDevelopmentEvidenceError("assessment requires exactly one configured model report")
    for model_id in model_ids:
        report = reports[model_id]
        if (
            report.get("status") not in {"complete", "infrastructure-invalid"}
            or report.get("lineage_id") != root.name
            or report.get("model_id") != model_id
            or report.get("trial_count") != trial_count
            or list(report.get("trials", [])) != expected_trials
            or [item.get("trial_id") for item in report.get("trial_metrics", [])] != expected_trials
            or not isinstance(report_refs.get(model_id), Mapping)
            or not _is_bound_report_path(
                version, attempt, trial_count, model_id, report_refs[model_id].get("path"),
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
            **{key: screened[key] for key in ("p1", "p2", "p2_passes", "unavailable_trials", "schema_after_format_repair_rate", "arch_semantic_first_pass_rate", "truncations", "infrastructure_invalid", "repeated_gate_failures")},
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
            "tuple": [screened["p2"], screened["p1"], screened["arch_semantic_first_pass_rate"], screened["schema_after_format_repair_rate"], -screened["cost_usd"]],
            "trial_ids": screened["trial_ids"],
        }
    screening_pass = models[model_ids[0]]["screening_pass"]
    ambiguity = "none"
    if not screening_pass:
        for model_id in model_ids:
            screened = _screen_model(reports[model_id])
            if _leave_one_out_sensitive(reports[model_id], screened["screening_pass"]):
                ambiguity = "single_sample_sensitive"
                break
        if ambiguity == "none":
            for model_id in model_ids:
                metrics = reports[model_id].get("metrics", {})
                p1_ok = metrics.get("p1") == 1.0
                semantic_ok = isinstance(metrics.get("arch_semantic_first_pass_rate"), (int, float)) and metrics["arch_semantic_first_pass_rate"] >= 0.8
                p2_ok = isinstance(metrics.get("p2"), (int, float)) and metrics["p2"] >= 0.60
                if p2_ok != semantic_ok or p1_ok != p2_ok:
                    ambiguity = "metric_conflict"
                    break
    return {"schema_version": "4.0", "lineage_id": root.name, "version": version, "model_slot": model_ids[0], "trial_count": trial_count, "attempt": attempt, "status": "complete", "screening_pass": screening_pass, "ambiguity": ambiguity, "models": models}


def _fallback_tuple(assessment: Mapping[str, Any]) -> tuple[float, float, float, float, float]:
    models = list(assessment["models"].values())
    if len(models) != 1:
        raise PromptDevelopmentEvidenceError("baseline fallback requires exactly one model slot")
    return (
        float(models[0]["p2"]),
        float(models[0]["p1"]),
        float(models[0]["arch_semantic_first_pass_rate"]),
        float(models[0]["schema_after_format_repair_rate"]),
        float(models[0]["tuple"][4]),
    )


def _compare_fallback(left: tuple[float, float, float, float, float], right: tuple[float, float, float, float, float]) -> int:
    for index in range(5):
        if left[index] != right[index]:
            return 1 if left[index] > right[index] else -1
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
        initial_prompt_source_path: str | Path | None = None,
        repair_prompt_source_path: str | Path | None = None,
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
        self.initial_prompt_source_path = Path(initial_prompt_source_path).resolve() if initial_prompt_source_path is not None else self.prompt_source_path
        self.repair_prompt_source_path = Path(repair_prompt_source_path).resolve() if repair_prompt_source_path is not None else self.prompt_source_path
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
        initial_prompt_source_path: str | Path | None = None,
        repair_prompt_source_path: str | Path | None = None,
        require_environment: bool = True,
        spec_path: str | Path = "gold_file/specIR.json",
        target_path: str | Path | None = None,
        test_bundle_path: str | Path | None = None,
    ) -> "PromptDevelopmentCoordinator":
        preflight = preflight_calibration_config(config_path, context_limits_path, require_environment=require_environment)
        bundle = _source_prompt_bundle(
            Path(initial_prompt_source_path).resolve() if initial_prompt_source_path is not None else (Path(prompt_source_path).resolve() if prompt_source_path is not None else None),
            Path(repair_prompt_source_path).resolve() if repair_prompt_source_path is not None else (Path(prompt_source_path).resolve() if prompt_source_path is not None else None),
        )
        declaration = CalibrationBatchDeclaration(
            trial_count=3, semantic_repair_depth=2, repair_mode="patch", context_window_tokens=preflight.context_limits,
            spec=spec_path, target_profile=target_path or preflight.config.assets.target_profile, test_bundle=test_bundle_path or preflight.config.assets.test_bundle,
        )
        driver = ArchitectureCalibrationDriver(preflight.config, runs_root=runs_root, provider_factory=provider_factory, prompt_bundle=bundle)
        prepared, planning, manifest, constraints = driver._prepare(declaration)
        for prompt in bundle.values():
            scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        targets = declaration.targets(preflight.config)
        lineage_store, lineage = driver._publish_lineage(prepared, planning, manifest, constraints, targets, declaration)
        root = lineage_store.root
        config_ref = _publish_bytes(root, "prompt-development/config.json", canonical_json_bytes(preflight.config_snapshot))
        context_ref = _publish_bytes(root, "prompt-development/context_limits.json", preflight.context_bytes)
        protocol = {
            "schema_version": "4.0", "lineage_id": lineage["lineage_id"], "status": "initialized", "model_slot": next(iter(preflight.config.calibration_models)), "versions": list(_DEV_VERSIONS),
            "semantic_depth": 2, "base_trial_count": 3, "max_revisions": 2, "initial_trial_ceiling": 9, "repair_mode": "patch", "config_ref": config_ref, "context_limits_ref": context_ref,
            "api_key_env": preflight.config.providers[next(iter(preflight.config.calibration_models.values())).provider].api_key_env, "metric_definition": METRIC_DEFINITION,
            "screening": {
                "trial_count": 3,
                "p2_required_passes": 2,
                "max_effective_repairs": 2,
            },
            "fallback_order": ["second_repair_passes", "first_repair_passes", "initial_passes", "format_correct", "lower_cost", "earlier_version"],
            "components": {name: dict(ref) for name, ref in lineage["components"].items()},
            "bundle_unit": "initial-repair", "stages": ["initial", "repair"],
        }
        _publish_json(root, "prompt-development/protocol.json", protocol, "protocol")
        coordinator = cls(root, config_path=config_path, context_limits_path=context_limits_path, provider_factory=provider_factory, prompt_source_path=prompt_source_path, initial_prompt_source_path=initial_prompt_source_path, repair_prompt_source_path=repair_prompt_source_path, require_environment=require_environment, spec_path=spec_path, target_path=target_path or preflight.config.assets.target_profile, test_bundle_path=test_bundle_path or preflight.config.assets.test_bundle)
        coordinator._publish_version_snapshot("v0", bundle, previous_version=None)
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

    def _source_bundle(self) -> dict[str, bytes]:
        return _source_prompt_bundle(self.initial_prompt_source_path, self.repair_prompt_source_path)

    def _publish_version_snapshot(self, version: str, bundle: Mapping[str, bytes], *, previous_version: str | None) -> dict[str, Any]:
        protocol, lineage = _load_protocol(self.root)
        if version not in protocol["versions"] or (version == "v0" and previous_version is not None) or (version != "v0" and previous_version is None):
            raise PromptDevelopmentError("invalid prompt version ordering")
        if set(bundle) != set(ARCHITECTURE_PROMPT_PATHS) or any(not isinstance(data, bytes) for data in bundle.values()):
            raise PromptDevelopmentError("a prompt version requires exactly initial and repair source bytes")
        for prompt in bundle.values():
            scan_prompt_neutrality(prompt)
        version_dir = _version_dir(version)
        stage_refs = {phase: _publish_bytes(self.root, f"{version_dir}/{phase}.md", bundle[phase]) for phase in ARCHITECTURE_PROMPT_PATHS}
        snapshot = {"schema_version": "4.0", "lineage_id": lineage["lineage_id"], "version": version, "initial_ref": stage_refs["initial"], "repair_ref": stage_refs["repair"], "byte_encoding": "utf-8-raw-template"}
        snapshot_ref = _publish_json(self.root, f"{version_dir}/snapshot.json", snapshot, "snapshot")
        # Keep a read-only compatibility view for historical tools; runtime
        # calls and all active bindings use the two stage references above.
        _publish_bytes(self.root, f"{version_dir}/prompt.md", bundle["initial"])
        version_record = {"schema_version": "4.0", "lineage_id": lineage["lineage_id"], "version": version, "status": "admitted", "protocol_ref": _root_ref(self.root, "prompt-development/protocol.json"), "bundle_ref": snapshot_ref, "semantic_depth": 2, "base_trial_count": 3, "model_slot": protocol["model_slot"], "repair_mode": "patch"}
        _publish_json(self.root, f"{version_dir}/version.json", version_record, "version")
        return version_record

    def _check_source_snapshot(self, version_record: Mapping[str, Any], bundle: Mapping[str, bytes] | bytes) -> None:
        expected = _load_bundle(self.root, str(version_record["version"]))
        supplied = {"initial": bundle, "repair": bundle} if isinstance(bundle, bytes) else dict(bundle)
        if supplied != expected or self._source_bundle() != expected:
            raise PromptDevelopmentEvidenceError("repository prompt bundle drifted from admitted snapshot")
        if version_record.get("bundle_ref") != _root_ref(self.root, f"{_version_dir(version_record['version'])}/snapshot.json"):
            raise PromptDevelopmentEvidenceError("prompt version bundle reference drift")

    def _next_attempt(self, version: str) -> int:
        path = self.root / _version_dir(version) / "attempts"
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
                            assessment_path = self.root / _assessment_path_for_attempt(version, number)
                            if not assessment_path.is_file():
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
        if numbers:
            raise PromptDevelopmentError("a failed prompt-version attempt cannot be rerun; preserve it and start a fresh lineage after fixing the implementation")
        return 1

    def _next_retry_attempt(self, version: str) -> int:
        """Allocate the next attempt for an explicitly authorized sample retry."""

        attempts_root = self.root / _version_dir(version) / "attempts"
        numbers: list[int] = []
        incomplete_retry: list[int] = []
        if attempts_root.is_dir():
            for child in attempts_root.iterdir():
                match = re.fullmatch(r"attempt_(\d{3})", child.name)
                if not match:
                    continue
                number = int(match.group(1))
                numbers.append(number)
                declaration_path = child / "declaration.json"
                outcome_path = child / "outcome.json"
                if not declaration_path.is_file():
                    raise PromptDevelopmentError(f"retry cannot proceed with incomplete attempt {number}")
                declaration = _load_json(self.root, str(declaration_path.relative_to(self.root)), "attempt_declaration")
                if declaration.get("lineage_id") != self.root.name or declaration.get("version") != version or declaration.get("attempt") != number:
                    raise PromptDevelopmentEvidenceError(f"retry attempt {number} binding drift")
                if not outcome_path.is_file():
                    if declaration.get("retry", {}).get("authorization") != "explicit_user_authorization":
                        raise PromptDevelopmentError(f"retry cannot proceed with incomplete attempt {number}")
                    incomplete_retry.append(number)
                    continue
                outcome = _load_json(self.root, str(outcome_path.relative_to(self.root)), "attempt_outcome")
                if outcome.get("lineage_id") != self.root.name or outcome.get("version") != version or outcome.get("attempt") != number:
                    raise PromptDevelopmentEvidenceError(f"retry attempt {number} outcome binding drift")
        if numbers and sorted(numbers) != list(range(1, max(numbers) + 1)):
            raise PromptDevelopmentEvidenceError("attempt numbering is not monotonic")
        if len(incomplete_retry) > 1 or (incomplete_retry and incomplete_retry[0] != max(numbers)):
            raise PromptDevelopmentError("only the latest authorized retry attempt may be resumed")
        if incomplete_retry:
            return incomplete_retry[0]
        next_attempt = max(numbers, default=1) + 1
        if next_attempt > 3:
            raise PromptDevelopmentError("the trial-local retry allowance of two additional attempts is exhausted")
        return next_attempt

    def _declared_initial_trial_count(self) -> int:
        """Count only declared depth-zero trials in this lineage."""

        total = 0
        for version in _DEV_VERSIONS:
            attempts_root = self.root / _version_dir(version) / "attempts"
            if not attempts_root.is_dir():
                continue
            declared_trial_ids: set[str] = set()
            for declaration_path in attempts_root.glob("attempt_*/declaration.json"):
                declaration = _load_json(self.root, str(declaration_path.relative_to(self.root)), "attempt_declaration")
                if declaration.get("repair_mode") != "patch" or declaration.get("semantic_depth") != 2 or declaration.get("trial_count") != 3:
                    raise PromptDevelopmentEvidenceError("attempt declaration controls drift")
                trial_ids = tuple(declaration.get("initial_trial_ids", []))
                if not declared_trial_ids:
                    declared_trial_ids.update(trial_ids)
                    total += len(trial_ids)
                elif set(trial_ids) != declared_trial_ids:
                    raise PromptDevelopmentEvidenceError("attempt declarations do not preserve the fixed trial identities")
        return total

    def _selection_exists(self) -> bool:
        return any(
            (self.root / f"prompt-development/{name}").is_file()
            for name in ("selection.json", "diagnostic-selection.json", "selection-tie.json")
        )

    def _source_guard(self, admitted: Mapping[str, bytes] | bytes) -> Callable[[], None]:
        def guard() -> None:
            expected = {"initial": admitted, "repair": admitted} if isinstance(admitted, bytes) else dict(admitted)
            if self._source_bundle() != expected:
                raise CalibrationDeclarationError("ArchitecturePlanner prompt bundle drifted during the admitted attempt")
        return guard

    def _check_attempt_bundle(self, version: str, attempt: int, bundle: Mapping[str, bytes]) -> None:
        """Require each model root to carry the exact same two source bytes."""

        protocol, _lineage = _load_protocol(self.root)
        for model_id in (protocol["model_slot"],):
            model_root = self.root / version / (f"attempt_{attempt:03d}/" if attempt > 1 else "") / model_id
            batch_path = model_root / "batch.json"
            try:
                batch = json.loads(batch_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PromptDevelopmentEvidenceError(f"unable to load {version}/{model_id} batch: {exc}") from exc
            refs = batch.get("prompt_stage_refs")
            if not isinstance(refs, Mapping) or set(refs) != set(ARCHITECTURE_PROMPT_PATHS):
                raise PromptDevelopmentEvidenceError(f"{version}/{model_id} does not publish both prompt stages")
            for phase in ARCHITECTURE_PROMPT_PATHS:
                if _verify_root_ref(model_root, refs[phase], f"{version}/{model_id}/{phase} prompt") != bundle[phase]:
                    raise PromptDevelopmentEvidenceError(f"{version}/{model_id} prompt bundle bytes do not match the admitted version")

    def run_version(self, version: str = "v0") -> dict[str, Any]:
        if self._selection_exists():
            raise PromptDevelopmentError("prompt development is already selected; no later provider I/O is allowed")
        preflight = self._preflight()
        _protocol, lineage = _load_protocol(self.root, preflight)
        planning, constraints = _frozen_planning_context(self.root, lineage)
        if version != "v0":
            version_number = int(version[1:])
            previous = f"v{version_number - 1}"
            previous_assessment = _load_assessment(self.root, previous, 3)
            if previous_assessment.get("status") != "complete" or previous_assessment.get("screening_pass"):
                raise PromptDevelopmentError("later prompt versions require complete failing prior-version evidence")
            revision_path = self.root / f"{_version_dir(version)}/revision.json"
            if not revision_path.is_file():
                raise PromptDevelopmentError("prompt version has no committed evidence-backed revision")
            revision = _load_json(self.root, str(revision_path.relative_to(self.root)), "revision")
            previous_record = _load_version(self.root, previous)
            current_record = _load_version(self.root, version)
            if revision.get("lineage_id") != self.root.name or revision.get("previous_version") != previous or revision.get("new_bundle_ref") != current_record.get("bundle_ref"):
                raise PromptDevelopmentEvidenceError("revision and prompt-bundle version references do not agree")
        if (self.root / f"{_version_dir(version)}/assessment-n003.json").is_file():
            raise PromptDevelopmentError("a completed prompt version cannot be rerun")
        if self._declared_initial_trial_count() + 3 > 9:
            raise PromptDevelopmentError("baseline initial-generation ceiling of 9 would be exceeded")
        version_record = _load_version(self.root, version)
        bundle = _load_bundle(self.root, version)
        self._check_source_snapshot(version_record, bundle)
        for prompt in bundle.values():
            scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        attempt = self._next_attempt(version)
        attempt_dir = f"{_version_dir(version)}/attempts/attempt_{attempt:03d}"
        model_slot = preflight.model_projection.keys().__iter__().__next__()
        declaration = {
            "schema_version": "4.0", "lineage_id": self.root.name, "version": version,
            "attempt": attempt, "status": "declared", "bundle_ref": dict(version_record["bundle_ref"]), "trial_count": 3,
            "semantic_depth": 2, "repair_mode": "patch", "model_slot": model_slot,
            "initial_trial_ids": [f"trial_{index:03d}" for index in range(1, 4)],
        }
        _publish_json(self.root, f"{attempt_dir}/declaration.json", declaration, "attempt_declaration")
        driver = ArchitectureCalibrationDriver(preflight.config, runs_root=self.root.parents[2], provider_factory=self.provider_factory, prompt_bundle=bundle, prompt_source_guard=self._source_guard(bundle))
        spec_bytes, target_bytes, bundle_bytes = self._frozen_batch_inputs(lineage)
        batch = CalibrationBatchDeclaration(
            prompt_version=version, trial_count=3, semantic_repair_depth=2, repair_mode="patch", context_window_tokens=preflight.context_limits,
            spec=spec_bytes, target_profile=target_bytes, test_bundle=bundle_bytes, attempt=attempt,
        )
        reports: dict[str, dict[str, Any]] = {}
        report_refs: dict[str, dict[str, str]] = {}
        status = "complete"
        try:
            driver.run(batch)
            self._check_source_snapshot(version_record, bundle)
            self._check_attempt_bundle(version, attempt, bundle)
            for model_id in (model_slot,):
                model_root = self.root / version / (f"attempt_{attempt:03d}/" if attempt > 1 else "") / model_id
                report = _recompute_lineage_report(self.root, model_root, config=preflight.config)
                reports[model_id] = report
                report_path = model_root / "calibration_report.json"
                report_refs[model_id] = _report_ref(self.root, str(report_path.relative_to(self.root))) if report_path.is_file() else None
            self._check_source_snapshot(version_record, bundle)
            assessment = _assessment_from_reports(self.root, version, attempt, reports, report_refs, 3)
        except (CalibrationError, PromptDevelopmentEvidenceError) as exc:
            failed_refs = dict(report_refs)
            for model_id in (model_slot,):
                model_root = self.root / version / (f"attempt_{attempt:03d}/" if attempt > 1 else "") / model_id
                existing = model_root / "calibration_report.json"
                if existing.is_file():
                    failed_refs[model_id] = _report_ref(self.root, str(existing.relative_to(self.root)))
            failed_outcome = {"schema_version": "4.0", "lineage_id": self.root.name, "version": version, "attempt": attempt, "status": "infrastructure-invalid", "model_slot": model_slot, "reports": {model_slot: failed_refs.get(model_slot)}}
            _publish_json(self.root, f"{attempt_dir}/outcome.json", failed_outcome, "attempt_outcome")
            raise PromptDevelopmentError(f"prompt development attempt {attempt} failed before complete evidence: {type(exc).__name__}") from exc
        outcome = {"schema_version": "4.0", "lineage_id": self.root.name, "version": version, "attempt": attempt, "status": status, "model_slot": model_slot, "reports": report_refs}
        outcome_ref = _publish_json(self.root, f"{attempt_dir}/outcome.json", outcome, "attempt_outcome")
        if status != "complete":
            return outcome
        assessment_ref = _publish_json(self.root, f"{_version_dir(version)}/assessment-n003.json", assessment, "assessment")
        version_outcome = {"schema_version": "4.0", "lineage_id": self.root.name, "version": version, "status": "complete", "assessment_ref": assessment_ref, "conclusion": "baseline selected" if assessment["screening_pass"] else "baseline not reached"}
        _publish_json(self.root, f"{_version_dir(version)}/outcome.json", version_outcome, "outcome")
        if assessment["screening_pass"]:
            self.select(version, assessment=assessment, assessment_ref=assessment_ref, reason="first passing version")
        elif version == "v2":
            self.select(version, assessment=assessment, assessment_ref=assessment_ref, reason="diagnostic comparison after V2")
        return {"outcome": outcome, "assessment": assessment, "outcome_ref": outcome_ref}

    def retry_failed_trials(self, version: str = "v1") -> dict[str, Any]:
        """Retry only the fixed N=3 infrastructure-failed samples of one attempt.

        A retry gets a fresh immutable attempt directory, while the prior
        attempt and its assessment remain untouched.  The current protocol
        only admits a whole-version retry when every sample failed before
        semantic evaluation; this is the state of the interrupted V1 run.
        """

        if self._selection_exists():
            raise PromptDevelopmentError("prompt development is already selected; no later provider I/O is allowed")
        if version not in _DEV_VERSIONS:
            raise PromptDevelopmentError("version must be v0, v1, or v2")
        preflight = self._preflight()
        _protocol, lineage = _load_protocol(self.root, preflight)
        planning, constraints = _frozen_planning_context(self.root, lineage)
        parent_attempt, parent_assessment_path, parent_assessment = _assessment_record(self.root, version, 3)
        if parent_assessment.get("status") != "complete" or parent_assessment.get("screening_pass"):
            raise PromptDevelopmentError("sample retry requires a complete failing assessment")
        model_slot = str(parent_assessment["model_slot"])
        model_record = parent_assessment.get("models", {}).get(model_slot)
        if not isinstance(model_record, Mapping):
            raise PromptDevelopmentEvidenceError("assessment does not contain the configured model slot")
        report = _read_json_ref(self.root, model_record["report_ref"], f"{version}/{model_slot}/report")
        expected_trials = [f"trial_{index:03d}" for index in range(1, 4)]
        trial_metrics = report.get("trial_metrics")
        if (
            report.get("lineage_id") != self.root.name
            or report.get("prompt_version") != version
            or report.get("model_id") != model_slot
            or report.get("trial_count") != 3
            or not isinstance(trial_metrics, list)
            or [item.get("trial_id") for item in trial_metrics] != expected_trials
        ):
            raise PromptDevelopmentEvidenceError("retry source report is not a coherent N=3 assessment report")
        failed_trials = [item["trial_id"] for item in trial_metrics if item.get("terminal") == "infrastructure-invalid"]
        if failed_trials != expected_trials:
            raise PromptDevelopmentError(
                "sample retry is limited to a complete all-infrastructure-failed attempt; completed samples must not be rerun"
            )
        attempt = self._next_retry_attempt(version)
        version_record = _load_version(self.root, version)
        bundle = _load_bundle(self.root, version)
        self._check_source_snapshot(version_record, bundle)
        for prompt in bundle.values():
            scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        attempt_dir = f"{_version_dir(version)}/attempts/attempt_{attempt:03d}"
        declaration = {
            "schema_version": "4.0", "lineage_id": self.root.name, "version": version,
            "attempt": attempt, "status": "declared", "bundle_ref": dict(version_record["bundle_ref"]), "trial_count": 3,
            "semantic_depth": 2, "repair_mode": "patch", "model_slot": model_slot,
            "initial_trial_ids": expected_trials,
            "retry": {
                "authorization": "explicit_user_authorization",
                "reason": "retry V1 infrastructure-failed samples after model service recovery",
                "parent_assessment_ref": _root_ref(self.root, parent_assessment_path),
                "trial_ids": failed_trials,
            },
        }
        _publish_json(self.root, f"{attempt_dir}/declaration.json", declaration, "attempt_declaration")
        driver = ArchitectureCalibrationDriver(
            preflight.config,
            runs_root=self.root.parents[2],
            provider_factory=self.provider_factory,
            prompt_bundle=bundle,
            prompt_source_guard=self._source_guard(bundle),
            lineage_component_bytes={
                name: _verify_root_ref(self.root, ref, f"lineage/components/{name}")
                for name, ref in lineage.get("components", {}).items()
            },
        )
        spec_bytes, target_bytes, bundle_bytes = self._frozen_batch_inputs(lineage)
        batch = CalibrationBatchDeclaration(
            prompt_version=version, trial_count=3, semantic_repair_depth=2, repair_mode="patch",
            context_window_tokens=preflight.context_limits,
            prepared_inputs=prepare_architecture_inputs(spec_bytes, target_bytes, bundle_bytes),
            attempt=attempt, trial_ids=tuple(failed_trials),
        )
        reports: dict[str, dict[str, Any]] = {}
        report_refs: dict[str, dict[str, str] | None] = {}
        try:
            recorded_components = {
                name: _verify_root_ref(self.root, ref, f"lineage/components/{name}")
                for name, ref in lineage.get("components", {}).items()
            }
            original_components = _architecture._default_components
            _architecture._default_components = lambda *args, **kwargs: dict(recorded_components)
            try:
                driver.run(batch)
            finally:
                _architecture._default_components = original_components
            self._check_source_snapshot(version_record, bundle)
            self._check_attempt_bundle(version, attempt, bundle)
            model_root = self.root / version / f"attempt_{attempt:03d}" / model_slot
            rebuilt_report = _recompute_lineage_report(self.root, model_root, config=preflight.config)
            reports[model_slot] = rebuilt_report
            report_path = model_root / "calibration_report.json"
            if not report_path.is_file():
                raise PromptDevelopmentEvidenceError("retry did not publish a calibration report")
            report_refs[model_slot] = _report_ref(self.root, str(report_path.relative_to(self.root)))
            assessment = _assessment_from_reports(self.root, version, attempt, reports, report_refs, 3)
        except (CalibrationError, PromptDevelopmentEvidenceError) as exc:
            failed_ref = None
            model_root = self.root / version / f"attempt_{attempt:03d}" / model_slot
            existing = model_root / "calibration_report.json"
            if existing.is_file():
                failed_ref = _report_ref(self.root, str(existing.relative_to(self.root)))
            failed_outcome = {
                "schema_version": "4.0", "lineage_id": self.root.name, "version": version,
                "attempt": attempt, "status": "infrastructure-invalid", "model_slot": model_slot,
                "reports": {model_slot: failed_ref},
            }
            _publish_json(self.root, f"{attempt_dir}/outcome.json", failed_outcome, "attempt_outcome")
            raise PromptDevelopmentError(f"authorized retry failed before complete evidence: {type(exc).__name__}") from exc
        outcome = {
            "schema_version": "4.0", "lineage_id": self.root.name, "version": version,
            "attempt": attempt, "status": "complete", "model_slot": model_slot, "reports": report_refs,
        }
        outcome_ref = _publish_json(self.root, f"{attempt_dir}/outcome.json", outcome, "attempt_outcome")
        assessment_ref = _publish_json(self.root, _assessment_path_for_attempt(version, attempt), assessment, "assessment")
        recomputed = self.recompute(version, 3, require_source_match=True)
        if recomputed["screening_pass"]:
            self.select(version, assessment=recomputed, assessment_ref=assessment_ref, reason="first passing version after authorized infrastructure-failed sample retry")
        return {"outcome": outcome, "assessment": recomputed, "outcome_ref": outcome_ref, "assessment_ref": assessment_ref}

    def record_revision(
        self,
        version: str,
        *,
        hypothesis: str,
        evidence_refs: list[Mapping[str, Any]],
        expected_gates: list[str] | None = None,
        expected_metrics: list[str] | None = None,
        stopping_conclusion: str | None = None,
        prompt_bytes: bytes | None = None,
        initial_prompt_bytes: bytes | None = None,
        repair_prompt_bytes: bytes | None = None,
        changed_stage: str | None = None,
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
        previous = f"v{int(version[1:]) - 1}"
        previous_assessment = _load_assessment(self.root, previous, 3)
        if previous_assessment.get("status") != "complete" or previous_assessment.get("screening_pass"):
            raise PromptDevelopmentError("revision requires complete failing prior-version assessment")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise PromptDevelopmentError("revision requires a concrete reason")
        stopping = stopping_conclusion.strip() if isinstance(stopping_conclusion, str) else ""
        if not stopping:
            raise PromptDevelopmentError("revision requires a stopping conclusion")
        if not evidence_refs:
            raise PromptDevelopmentError("revision requires exact evidence references")
        evidence_paths: set[str] = set()
        for ref in evidence_refs:
            ref_path = str(ref.get("path", ""))
            if ref_path in evidence_paths:
                raise PromptDevelopmentEvidenceError("revision evidence references must be unique")
            evidence_paths.add(ref_path)
            if not ref_path.startswith(_version_dir(previous) + "/"):
                raise PromptDevelopmentEvidenceError("revision evidence must belong to the immediately previous version")
            _verify_root_ref(self.root, ref, "revision evidence")
            if ref_path.endswith("assessment-n003.json"):
                cited = _load_json(self.root, ref_path, "assessment")
                if cited.get("status") != "complete" or cited.get("screening_pass") is not False:
                    raise PromptDevelopmentEvidenceError("revision evidence must cite a complete failing assessment")
            elif "/calibration_report.json" in ref_path:
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
        old_bundle = _load_bundle(self.root, previous)
        legacy_single_source = self.prompt_source_path is not None and self.initial_prompt_source_path == self.prompt_source_path and self.repair_prompt_source_path == self.prompt_source_path
        if initial_prompt_bytes is not None or repair_prompt_bytes is not None:
            proposed = self._source_bundle()
            if initial_prompt_bytes is not None:
                proposed["initial"] = initial_prompt_bytes
            if repair_prompt_bytes is not None:
                proposed["repair"] = repair_prompt_bytes
        elif prompt_bytes is not None:
            proposed = {"initial": prompt_bytes, "repair": old_bundle["repair"]}
        else:
            proposed = self._source_bundle()
        if not all(isinstance(data, bytes) for data in proposed.values()):
            raise PromptDevelopmentError("revision prompt bundle must contain raw bytes")
        if legacy_single_source and initial_prompt_bytes is None and repair_prompt_bytes is None and prompt_bytes is None:
            proposed["repair"] = old_bundle["repair"]
        elif self._source_bundle() != proposed:
            raise PromptDevelopmentEvidenceError("proposed revision bundle does not match the repository prompt sources")
        for prompt in proposed.values():
            scan_prompt_neutrality(prompt, forbidden_tokens=_input_neutrality_tokens(planning, constraints, preflight.config))
        changed = [phase for phase in ARCHITECTURE_PROMPT_PATHS if proposed[phase] != old_bundle[phase]]
        if not changed:
            raise PromptDevelopmentError("revision must change at least one prompt stage")
        if changed_stage is not None and changed_stage not in ARCHITECTURE_PROMPT_PATHS:
            raise PromptDevelopmentError("changed_stage must be initial or repair")
        if changed_stage is not None and changed != [changed_stage]:
            raise PromptDevelopmentError("changed_stage may only be used when exactly that one stage changed")
        diffs = {
            stage: "".join(difflib.unified_diff(
                old_bundle[stage].decode("utf-8").splitlines(True),
                proposed[stage].decode("utf-8").splitlines(True),
                fromfile=f"{previous}/{stage}", tofile=f"{version}/{stage}",
            ))
            for stage in changed
        }
        new_record = self._publish_version_snapshot(version, proposed, previous_version=previous)
        revision = {
            "schema_version": "4.0", "lineage_id": self.root.name, "version": version,
            "previous_version": previous, "previous_bundle_ref": dict(_load_version(self.root, previous)["bundle_ref"]),
            "new_bundle_ref": dict(new_record["bundle_ref"]), "hypothesis": hypothesis.strip(),
            "evidence_refs": [dict(ref) for ref in evidence_refs], "expected_gates": gates,
            "expected_metrics": metrics, "changed_stages": changed, "unified_diffs": diffs,
            "stopping_conclusion": stopping,
            "protocol_ref": _root_ref(self.root, "prompt-development/protocol.json"),
            "status": "admitted",
        }
        _publish_json(self.root, f"{_version_dir(version)}/revision.json", revision, "revision")
        return new_record

    def recompute(self, version: str, count: int = 3, *, require_complete: bool = False, require_source_match: bool = False) -> dict[str, Any]:
        preflight = self._preflight()
        if count != 3:
            raise PromptDevelopmentError("current development recomputation requires exactly N=3")
        assessment = _load_assessment(self.root, version, 3)
        if require_complete:
            if assessment.get("status") != "complete" or not (self.root / "prompt-development/selection.json").is_file():
                raise PromptDevelopmentEvidenceError("complete recomputation requires a committed complete assessment and selection")
            selection = _load_json(self.root, "prompt-development/selection.json", "selection")
            assessment_path = _assessment_record(self.root, version, 3)[1]
            if selection.get("selected_version") != version or selection.get("assessment_ref", {}).get("path") != assessment_path:
                raise PromptDevelopmentEvidenceError("selection is not bound to the requested final assessment")
            _verify_root_ref(self.root, selection["assessment_ref"], "selection assessment")
            selected_record = _load_version(self.root, version)
            if selection.get("bundle_ref") != selected_record.get("bundle_ref"):
                raise PromptDevelopmentEvidenceError("selection prompt bundle binding drift")
        if require_source_match:
            version_record = _load_version(self.root, version)
            self._check_source_snapshot(version_record, _load_bundle(self.root, version))
        rebuilt_reports: dict[str, dict[str, Any]] = {}
        report_refs: dict[str, dict[str, str]] = {}
        model_ids = tuple(sorted(assessment.get("models", {})))
        if model_ids != (assessment.get("model_slot"),):
            raise PromptDevelopmentEvidenceError("assessment model slot binding drift")
        for model_id in model_ids:
            report_ref = assessment["models"][model_id]["report_ref"]
            report = _read_json_ref(self.root, report_ref, f"assessment/{model_id}/report")
            if report.get("lineage_id") != self.root.name or report.get("prompt_version") != version or report.get("trial_count") != count:
                raise PromptDevelopmentEvidenceError("assessment report binding drift")
            model_root = self.root / version / (f"attempt_{assessment['attempt']:03d}/" if assessment["attempt"] > 1 else "") / model_id
            expected_report = _expected_report_path(version, int(assessment["attempt"]), 3, model_id)
            if report_ref.get("path") != expected_report:
                raise PromptDevelopmentEvidenceError("assessment report path is not bound to its coherent N=3 attempt")
            rebuilt = _recompute_lineage_report(self.root, model_root, config=preflight.config)
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
            3,
        )
        if rebuilt_assessment != assessment:
            raise PromptDevelopmentEvidenceError("assessment summary is not a deterministic projection of report evidence")
        diagnostic_path = self.root / "prompt-development/diagnostic-selection.json"
        if diagnostic_path.is_file():
            tie = _load_json(self.root, "prompt-development/diagnostic-selection.json", "selection")
            expected_tuples: dict[str, list[float]] = {}
            for candidate in _DEV_VERSIONS:
                candidate_ref = tie.get("assessment_refs", {}).get(candidate)
                if not isinstance(candidate_ref, Mapping):
                    raise PromptDevelopmentEvidenceError("diagnostic selection is missing an assessment reference")
                candidate_assessment = _read_json_ref(self.root, candidate_ref, f"diagnostic selection/{candidate}/assessment")
                expected_tuples[candidate] = list(_fallback_tuple(candidate_assessment))
            if tie.get("status") != "diagnostic" or tie.get("comparison_tuples") != expected_tuples:
                raise PromptDevelopmentEvidenceError("diagnostic selection is not a deterministic fallback projection")
            if require_complete:
                raise PromptDevelopmentEvidenceError("a diagnostic selection cannot satisfy complete selection recomputation")
        return assessment

    def select(self, version: str, *, assessment: Mapping[str, Any] | None = None, assessment_ref: Mapping[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
        if self._selection_exists():
            if (self.root / "prompt-development/selection.json").is_file():
                path = "prompt-development/selection.json"
            elif (self.root / "prompt-development/diagnostic-selection.json").is_file():
                path = "prompt-development/diagnostic-selection.json"
            else:
                path = "prompt-development/selection-tie.json"
            return _load_json(self.root, path, "selection")
        assessments: dict[str, dict[str, Any]] = {}
        assessment_paths: dict[str, str] = {}
        for candidate in _DEV_VERSIONS:
            records = _assessment_records(self.root, candidate, 3)
            if records:
                _attempt, assessment_path, candidate_assessment = records[-1]
                assessments[candidate] = candidate_assessment
                assessment_paths[candidate] = assessment_path
        if assessment is not None:
            assessments[version] = dict(assessment)
            if assessment_ref is not None and isinstance(assessment_ref.get("path"), str):
                assessment_paths[version] = str(assessment_ref["path"])
            else:
                assessment_paths[version] = _assessment_record(self.root, version, 3)[1]
        if not assessments or version not in assessments:
            raise PromptDevelopmentError("selection requires a committed assessment")
        for candidate, value in list(assessments.items()):
            assessments[candidate] = self.recompute(candidate, int(value["trial_count"]))
        chosen: str | None = None
        for candidate in _DEV_VERSIONS:
            if candidate in assessments and assessments[candidate].get("screening_pass"):
                chosen = candidate
                break
        diagnostic = False
        if chosen is None and all(candidate in assessments and assessments[candidate].get("status") == "complete" for candidate in _DEV_VERSIONS):
            tuples = {candidate: _fallback_tuple(assessments[candidate]) for candidate in assessments}
            best = max(tuples.values(), key=lambda item: item)
            winners = [candidate for candidate, value in tuples.items() if value == best]
            chosen = next(candidate for candidate in _DEV_VERSIONS if candidate in winners)
            diagnostic = True
        elif chosen is None:
            return {"status": "not-ready"}
        if chosen is None:
            return {"status": "not-ready"}
        selected_assessment = assessments[chosen]
        version_record = _load_version(self.root, chosen)
        bundle = _load_bundle(self.root, chosen)
        if diagnostic:
            expected_bundle_ref = _root_ref(self.root, f"{_version_dir(chosen)}/snapshot.json")
            if version_record.get("bundle_ref") != expected_bundle_ref:
                raise PromptDevelopmentEvidenceError("diagnostic prompt bundle reference drift")
        else:
            self._check_source_snapshot(version_record, bundle)
        assessment_path = assessment_paths.get(chosen) or _assessment_record(self.root, chosen, 3)[1]
        tuples = {candidate: list(_fallback_tuple(value)) for candidate, value in assessments.items()}
        selection = {"schema_version": "4.0", "lineage_id": self.root.name, "status": "diagnostic" if diagnostic else "selected", "selected_version": chosen, "bundle_ref": dict(version_record["bundle_ref"]), "assessment_ref": _root_ref(self.root, assessment_path), "reason": reason or ("first version reaching the 2-of-3 baseline" if selected_assessment.get("screening_pass") else "best diagnostic version after V2; no handoff"), "comparison_tuples": tuples, "assessment_refs": {candidate: _root_ref(self.root, assessment_paths[candidate]) for candidate in assessments}}
        target = "prompt-development/diagnostic-selection.json" if diagnostic else "prompt-development/selection.json"
        _publish_json(self.root, target, selection, "selection")
        return selection

    def publish_handoff(self, approval_ref: Mapping[str, Any]) -> dict[str, str]:
        selection = _load_json(self.root, "prompt-development/selection.json", "selection")
        if selection.get("status") != "selected":
            raise PromptDevelopmentError("only a selected development prompt can produce a handoff")
        approval = _read_json_ref(self.root, approval_ref, "owner approval")
        approval_errors = sorted(
            Draft202012Validator(load_schema(_SCHEMA_NAMES["owner_approval"])).iter_errors(approval),
            key=lambda item: tuple(item.absolute_path),
        )
        if approval_errors:
            raise PromptDevelopmentEvidenceError(f"invalid owner approval: {approval_errors[0].message}")
        selected_version = selection.get("selected_version")
        bundle_ref = selection.get("bundle_ref")
        assessment_ref = selection.get("assessment_ref")
        if selected_version not in set(_DEV_VERSIONS) or not isinstance(bundle_ref, Mapping) or not isinstance(assessment_ref, Mapping):
            raise PromptDevelopmentEvidenceError("selected development record is not handoff-complete")
        handoff = {
            "schema_version": "4.0",
            "lineage_id": self.root.name,
            "consumer": "m1-4c",
            "selection_ref": _root_ref(self.root, "prompt-development/selection.json"),
            "selected_version": selected_version,
            "bundle_ref": dict(bundle_ref),
            "assessment_ref": dict(assessment_ref),
            "owner_approval_ref": dict(approval_ref),
            "selection_reason": selection.get("reason"),
            "satisfies": {
                "baseline_2_of_3": True,
                "protocol_neutrality": True,
                "owner_signature": True,
                "production_quality_proven": False,
            },
        }
        return _publish_json(self.root, "prompt-development/handoff.json", handoff, "handoff")

    def next_action(self, version: str) -> dict[str, Any]:
        if (self.root / "prompt-development/selection.json").is_file():
            return {"action": "terminal-selection"}
        if (self.root / "prompt-development/diagnostic-selection.json").is_file():
            return {"action": "terminal-diagnostic", "handoff": False}
        version_dir = self.root / _version_dir(version) / "attempts"
        outcomes = sorted(version_dir.glob("attempt_*/outcome.json")) if version_dir.is_dir() else []
        if not outcomes:
            return {"action": "run", "attempt": 1}
        latest = _load_json(self.root, str(outcomes[-1].relative_to(self.root)), "attempt_outcome")
        if latest["status"] == "infrastructure-invalid":
            return {"action": "inspect-invalid-attempt"}
        assessment_records = _assessment_records(self.root, version, 3)
        if not assessment_records:
            return {"action": "assess", "attempt": latest["attempt"]}
        _assessment_attempt, _assessment_relative, assessment = assessment_records[-1]
        if assessment.get("screening_pass"):
            return {"action": "terminal-selection", "version": version}
        if version == "v2":
            return {"action": "terminal-diagnostic", "handoff": False}
        return {"action": "record-revision", "version": f"v{int(version[1:]) + 1}", "parent": version}


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
    for version in _DEV_VERSIONS:
        assessment_records = _assessment_records(root, version, 3)
        if not assessment_records:
            continue
        assessment = coordinator.recompute(version, 3)
        _assessment_attempt, assessment_relative, _assessment_value = assessment_records[-1]
        version_record = _load_version(root, version)
        bundle = _load_json(root, f"{_version_dir(version)}/snapshot.json", "snapshot")
        slots: dict[str, Any] = {}
        for model_id in sorted(assessment["models"]):
            model = assessment["models"][model_id]
            report = _read_json_ref(root, model["report_ref"], f"summary/{version}/{model_id}/report")
            slots[model_id] = {
                "trial_count": 3,
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
            "trial_count": 3,
            "attempt": assessment["attempt"],
            "status": assessment["status"],
            "screening_pass": assessment["screening_pass"],
            "ambiguity": assessment.get("ambiguity"),
            "comparison_tuple": list(_fallback_tuple(assessment)),
            "assessment_ref": _root_ref(root, assessment_relative),
            "bundle_ref": dict(version_record["bundle_ref"]),
            "bundle": {
                "initial_ref": dict(bundle["initial_ref"]),
                "repair_ref": dict(bundle["repair_ref"]),
            },
            "slots": slots,
        }
    selection_path = root / "prompt-development/selection.json"
    tie_path = root / "prompt-development/diagnostic-selection.json"
    if selection_path.is_file():
        terminal = _load_json(root, "prompt-development/selection.json", "selection")
    elif tie_path.is_file():
        terminal = _load_json(root, "prompt-development/diagnostic-selection.json", "selection")
    else:
        terminal = {"status": "not-ready"}
    selected = terminal.get("status") == "selected"
    handoff_ref = _root_ref(root, "prompt-development/handoff.json") if selected and (root / "prompt-development/handoff.json").is_file() else None
    protocol_exceptions: list[dict[str, Any]] = []
    for version in _DEV_VERSIONS:
        attempts_root = root / _version_dir(version) / "attempts"
        if not attempts_root.is_dir():
            continue
        for declaration_path in sorted(attempts_root.glob("attempt_*/declaration.json")):
            declaration = _load_json(root, str(declaration_path.relative_to(root)), "attempt_declaration")
            retry = declaration.get("retry")
            if isinstance(retry, Mapping):
                protocol_exceptions.append({
                    "exception": "infrastructure_failed_sample_retry",
                    "authorization": retry["authorization"],
                    "model_id": declaration["model_slot"],
                    "trial_id": ",".join(retry["trial_ids"]),
                    "ref": _root_ref(root, str(declaration_path.relative_to(root))),
                })
    return {
        "schema_version": "4.0",
        "change": "m1-4a2-patch-calibration-rerun",
        "lineage_id": root.name,
        "lineage_root": f"runs/_calibration/s4-architecture/{root.name}/prompt-development",
        "controlled_artifacts": {
            "planning_index": dict(lineage["artifacts"]["planning_index"]),
            "delivery_constraints": dict(lineage["artifacts"]["delivery_constraints"]),
            "schema": dict(lineage["artifacts"]["schema"]),
            "validator": dict(lineage["artifacts"]["validator"]),
        },
        "model_slot": _protocol["model_slot"],
        "versions": versions,
        "terminal": terminal,
        "handoff_ref": handoff_ref,
        "protocol_exceptions": protocol_exceptions,
        "limitations": [
            "This is only M1-4a2 N=3 baseline usability evidence.",
            "Long-term prompt quality is not proven here and must be observed during complete framework runs.",
        ],
    }


def render_development_report(summary: Mapping[str, Any]) -> str:
    """Render Markdown from a machine summary without hand-entered metrics."""

    lines = [
        "# M1-4a2 development report",
        "",
        f"- Lineage: `{summary['lineage_id']}`",
        f"- Terminal status: `{summary['terminal'].get('status')}`",
        f"- Model slot: `{summary['model_slot']}`",
        "",
    ]
    if summary["terminal"].get("status") == "selected":
        lines.extend([
            f"- Selected version: `{summary['terminal']['selected_version']}`",
            f"- Selection reason: `{summary['terminal']['reason']}`",
            f"- Selected bundle: `{summary['terminal']['bundle_ref']['path']}`",
            f"- M1-4c handoff: `{summary['handoff_ref']['path']}`" if summary.get("handoff_ref") else "- M1-4c handoff: `awaiting owner approval`",
            "",
        ])
    for version, record in summary.get("versions", {}).items():
        lines.extend([f"## {version}", "", f"- Trials per slot: `{record['trial_count']}`", f"- Screening pass: `{record['screening_pass']}`", f"- Initial source: `{record['bundle']['initial_ref']['path']}`", f"- Repair source: `{record['bundle']['repair_ref']['path']}`", ""])
        lines.append("| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for model_id in sorted(record["slots"]):
            slot = record["slots"][model_id]
            identity = slot["model_identity"]
            lines.append(
                f"| {model_id} | {slot['p0']} | {slot['p1']} | {slot['p2']} | {slot['schema_after_format_repair_rate']} | {slot['arch_semantic_first_pass_rate']} | {slot['usage']['truncated']} | {slot['usage']['cost_usd']} | {json.dumps(identity['model_strings'], ensure_ascii=False, sort_keys=True)} |"
            )
        lines.extend(["", "### Slot diagnostics", ""])
        for model_id in sorted(record["slots"]):
            slot = record["slots"][model_id]
            lines.append(
                f"- `{model_id}`: infrastructure_invalid=`{slot['infrastructure_invalid']}`, repeated_initial_failures=`{json.dumps(slot['repeated_gate_failures'], sort_keys=True)}`, parameter_support=`{json.dumps(slot['model_identity']['parameter_support'], sort_keys=True)}`"
            )
        slot_names = sorted(record["slots"])
        lines.extend(["", "### Gate final pass rates", "", "| gate | " + " | ".join(slot_names) + " |", "| --- | " + " | ".join("---:" for _ in slot_names) + " |"])
        for gate in _GATES:
            values = [record["slots"][model_id]["gates"][gate]["rate"] for model_id in slot_names]
            lines.append(f"| {gate} | " + " | ".join(str(value) for value in values) + " |")
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
    output_dir: str | Path = "experiments/m1-4a2-patch-calibration-rerun/results",
) -> dict[str, Any]:
    summary = build_development_summary(
        development_root, config_path=config_path, context_limits_path=context_limits_path,
    )
    markdown = render_development_report(summary)
    validate_development_report(summary, markdown)
    destination = Path(output_dir).resolve()
    RunStore._write_atomic_at(destination / "development-summary.json", canonical_json_bytes(summary))
    RunStore._write_atomic_at(destination / "development-report.md", markdown.encode("utf-8"))
    return summary


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
        prompt_source_path: str | Path = "nepa/agents/prompts/architecture_planner_initial.md",
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
        prompt_source_path: str | Path = "nepa/agents/prompts/architecture_planner_initial.md",
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
    for name in ("run-version", "record-revision", "recompute"):
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
            command.add_argument("--stopping-conclusion", default=None)
        if name == "recompute":
            command.add_argument("--require-complete", action="store_true")
            command.add_argument("--require-source-match", action="store_true")
    retry = sub.add_parser("retry-failed-trials")
    retry.add_argument("--development-root", required=True)
    retry.add_argument("--config", default=None)
    retry.add_argument("--context-limits", default=None)
    retry.add_argument("--version", default="v1")
    select = sub.add_parser("select")
    select.add_argument("--development-root", required=True)
    select.add_argument("--config", default=None)
    select.add_argument("--context-limits", default=None)
    select.add_argument("--version", default="v2")
    approve = sub.add_parser("approve")
    approve.add_argument("--development-root", required=True)
    approve.add_argument("--config", default=None)
    approve.add_argument("--context-limits", default=None)
    approve.add_argument("--approval", required=True)
    report = sub.add_parser("report")
    report.add_argument("--development-root", required=True)
    report.add_argument("--config", required=True)
    report.add_argument("--context-limits", required=True)
    report.add_argument("--output-dir", default="experiments/m1-4a2-patch-calibration-rerun/results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
    elif args.command == "retry-failed-trials":
        value = coordinator.retry_failed_trials(args.version)
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
            evidence_refs = [_root_ref(coordinator.root, f"{_version_dir('v0')}/assessment-n003.json")]
            prompt_bytes = Path(args.prompt).read_bytes()
        value = coordinator.record_revision(args.version, hypothesis=hypothesis, evidence_refs=evidence_refs, expected_gates=record.get("expected_gates") if args.input else None, expected_metrics=record.get("expected_metrics") if args.input else None, stopping_conclusion=record.get("stopping_conclusion") if args.input else args.stopping_conclusion, prompt_bytes=prompt_bytes)
    elif args.command == "select":
        value = coordinator.select(args.version)
    elif args.command == "approve":
        approval_value = json.loads(Path(args.approval).read_text(encoding="utf-8"))
        if not isinstance(approval_value, dict) or approval_value.get("approved") is not True or not isinstance(approval_value.get("reviewer"), str):
            raise PromptDevelopmentError("approval must be a JSON object with approved=true and a reviewer")
        approval_ref = _publish_json(coordinator.root, "prompt-development/owner-approval.json", approval_value, "owner_approval")
        value = coordinator.publish_handoff(approval_ref)
    else:
        value = coordinator.recompute(args.version, 3, require_complete=args.require_complete, require_source_match=args.require_source_match)
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
