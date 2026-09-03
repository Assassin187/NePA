import base64
import hashlib
import json
from pathlib import Path

import pytest

from nepa.calibration.s4_architecture import (
    _CONTROLLED_COMPONENT_FILES,
    _source_bundle_bytes,
    build_lineage_manifest,
)
from nepa.config import load_config
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs


def test_lineage_excludes_prompt_identity_but_binds_components():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("qwen", "claude", "deepseek")}}})
    first = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, components={"prompt_label": b"v0"})
    same = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, components={"prompt_label": b"v1"})
    changed = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, components={"validator": b"changed"})
    assert first["lineage_id"] == same["lineage_id"]
    assert first["components"] == same["components"]
    assert first["lineage_id"] != changed["lineage_id"]
    assert set(first["providers"]) == {"fixture"}
    assert first["providers"]["fixture"]["sha256"]


def test_patch_lineage_binds_coupled_projection_contract():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("qwen", "claude", "deepseek")}}})
    lineage = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, repair_mode="patch")
    contract = lineage["repair_contract"]
    assert contract["coupled_projection_policy_version"] == "m1-4a2-coupled-layout-projection-v1"
    assert "coupled_projection_sha256" not in contract
    bundle = json.loads(_source_bundle_bytes(_CONTROLLED_COMPONENT_FILES["patch"]).decode("utf-8"))
    assert "nepa/schemas/architecture-patch-application.schema.json" in [item["path"] for item in bundle["files"]]


def test_referenced_provider_configuration_changes_lineage():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    base = {"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("qwen", "claude", "deepseek")}}}
    first = build_lineage_manifest(prepared, planning, manifest, constraints, config=load_config(overrides=base), model_targets=targets)
    changed_config = load_config(overrides={**base, "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://changed", "api_key_env": None}}})
    changed = build_lineage_manifest(prepared, planning, manifest, constraints, config=changed_config, model_targets=targets)
    assert first["lineage_id"] != changed["lineage_id"]


def test_configured_model_aliases_are_observations_not_lineage_identity():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    base = {"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}}
    prices = {
        f"fixture/{model}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}
        for model in ("qwen", "claude", "deepseek", "qwen-alias", "claude-alias", "deepseek-alias")
    }
    config = load_config(overrides={**base, "pricing": {"models": prices}})
    first_targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    alias_targets = {name: {"provider": "fixture", "model": f"{name}-alias", "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    first = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=first_targets)
    alias = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=alias_targets)
    assert first["lineage_id"] == alias["lineage_id"]
    assert first["models"]["qwen"]["model"] != alias["models"]["qwen"]["model"]


def test_controlled_source_bundles_are_explicit_sorted_and_auditable():
    root = Path(__file__).resolve().parents[1]
    for name, paths in _CONTROLLED_COMPONENT_FILES.items():
        bundle = json.loads(_source_bundle_bytes(paths).decode("utf-8"))
        entries = bundle["files"]
        assert bundle["format"] == "nepa-controlled-source-bundle-v1"
        assert [entry["path"] for entry in entries] == sorted(paths, key=lambda item: item.encode("utf-8"))
        for entry in entries:
            raw = base64.b64decode(entry["bytes_base64"], validate=True)
            assert raw == (root / entry["path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"]


def test_source_bundle_order_does_not_change_component_evidence():
    paths = _CONTROLLED_COMPONENT_FILES["provider_adapters"]
    assert _source_bundle_bytes(paths) == _source_bundle_bytes(tuple(reversed(paths)))


@pytest.mark.parametrize("component", ["agent_framework", "llm_runtime", "telemetry", "provider_adapters"])
def test_runtime_component_changes_create_a_new_lineage(component):
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("qwen", "claude", "deepseek")}}})
    first = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets)
    changed = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, components={component: b"controlled source changed"})
    assert first["lineage_id"] != changed["lineage_id"]


def test_batch_protocol_controls_are_not_lineage_identity(tmp_path):
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("qwen", "claude", "deepseek")}}})
    first = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, statistics={"metric_definition": "m1-4a1-architecture-calibration-metrics-v1"})
    second = build_lineage_manifest(prepared, planning, manifest, constraints, config=config, model_targets=targets, statistics={"metric_definition": "m1-4a1-architecture-calibration-metrics-v1"})
    assert first["lineage_id"] == second["lineage_id"]
    assert "trial_count" not in first["statistics"] and "semantic_depth" not in first["statistics"]
