import json
import re
from pathlib import Path

from nepa.agents.base import PromptRenderer
from nepa.agents.roles import get_role
from nepa.calibration.s4_architecture import build_lineage_manifest
from nepa.config import load_config
from nepa.schemas import architecture_draft_contract
from nepa.speclib.architecture import validate_architecture
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs


def test_planning_and_calibration_assets_are_protocol_neutral():
    paths = [
        Path("nepa/speclib/planning.py"),
        Path("nepa/speclib/delivery.py"),
        Path("nepa/speclib/architecture.py"),
        Path("nepa/calibration/s4_architecture.py"),
        Path("nepa/agents/prompts/architecture_planner.md"),
        *[Path("nepa/schemas") / name for name in (
            "architecture-draft.schema.json", "architecture-validation.schema.json", "calibration-lineage.schema.json",
            "calibration-batch.schema.json", "trial-request-ref.schema.json", "trial-response-ref.schema.json",
            "trial-validation.schema.json", "calibration-report.schema.json",
        )],
        *[Path("nepa/schemas/examples") / name for name in (
            "architecture-draft.example.json", "architecture-validation.example.json", "calibration-lineage.example.json",
            "calibration-batch.example.json", "trial-request-ref.example.json", "trial-response-ref.example.json",
            "trial-validation.example.json", "calibration-report.example.json",
        )],
    ]
    forbidden_protocol_tokens = (
        "mqtt", "connack", "mosquitto", "subscribe", "topic_filter", "mqtt_varint",
        "mqtt_utf8_string", "mqtt_requested_qos", "mqtt_subscription_entry", "mqtt_suback_return_code",
        "1883", "REQ-CONNECT-001", "REQ-SUBSCRIBE-001", "paho",
    )
    for path in paths:
        lowered = path.read_text(encoding="utf-8").lower()
        for token in forbidden_protocol_tokens:
            assert re.search(rf"\b{re.escape(token)}\b", lowered) is None, (path, token)
    prompt = Path("nepa/agents/prompts/architecture_planner.md").read_text(encoding="utf-8").lower()
    for token in ("anthropic", "openai", "qwen", "deepseek", "claude"):
        assert re.search(rf"\b{re.escape(token)}\b", prompt) is None


def test_alternate_application_fixture_uses_the_shared_path():
    root = Path("tests/fixtures/non_mqtt_application")
    prepared = prepare_architecture_inputs(root / "spec.json", root / "target.json", root / "test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    index = build_planning_index(prepared, manifest, constraints)
    assert constraints["naming"]["symbol_prefix"] == "orbitnet"
    assert index["protocol"]["name"] == "OrbitNet"
    assert all("source_ref" not in repr(value) for value in (index, constraints))


def test_alternate_application_fixture_reaches_architecture_validation_and_lineage():
    root = Path("tests/fixtures/non_mqtt_application")
    prepared = prepare_architecture_inputs(root / "spec.json", root / "target.json", root / "test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    index = build_planning_index(prepared, manifest, constraints)
    by_rule = {}
    for slot in constraints["file_slots"]:
        by_rule.setdefault(slot["rule_id"], []).append(slot["path"])
    contracts = [
        {"id": "session-contract", "purpose": "session", "owner": "s5", "interface_files": constraints["internal_interface_slots"][0]["interface_files"], "ready_gate": "s5", "provider": "s5", "consumers": ["codec", "net"]},
        {"id": "network-contract", "purpose": "network", "owner": "s5", "interface_files": constraints["internal_interface_slots"][1]["interface_files"], "ready_gate": "s5", "provider": "s5", "consumers": ["session"]},
    ]
    modules = [
        {"id": "codec", "name": "Codec", "purpose": "codec", "responsibilities": ["encode"], "non_goals": ["no io"], "owns_files": by_rule["message-codecs"]},
        {"id": "session", "name": "Session", "purpose": "session", "responsibilities": ["state"], "non_goals": ["no codec"], "owns_files": by_rule["session-source"]},
        {"id": "net", "name": "Network", "purpose": "network", "responsibilities": ["accept"], "non_goals": ["no codec"], "owns_files": by_rule["net-source"] + by_rule["server-entry-source"]},
    ]
    for module in modules:
        module["provides_contracts"] = []
        module["consumes_contracts"] = [contract["id"] for contract in contracts if module["id"] in contract["consumers"]]
    work_packages = []
    for module in modules:
        work_packages.append({
            "id": f"wp-{module['id']}", "title": module["name"], "goal": "complete the module", "module": module["id"], "kind": "implementation",
            "context_refs": [], "requirement_responsibilities": [], "allowed_files": module["owns_files"], "provides_contracts": [],
            "consumes_contracts": module["consumes_contracts"], "depends_on": [], "acceptance": {"outcome": "done"},
        })
    work_packages[1]["requirement_responsibilities"] = [
        {"req_id": item["id"], "role": "primary"} for item in index["requirements"] if item["level"] != "DEFINITION"
    ]
    draft = {"schema_version": "1.0", "decisions": [], "assumptions": ["application facts come from the fixture"], "contracts": contracts, "modules": modules, "work_packages": work_packages}
    validation = validate_architecture(draft, index, manifest, constraints)
    assert validation["verdict"] == "pass"
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 128, "context_window_tokens": 10000} for name in ("claude", "qwen", "deepseek")}
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("claude", "qwen", "deepseek")}}})
    lineage = build_lineage_manifest(prepared, index, manifest, constraints, config=config, model_targets=targets)
    schema, example = architecture_draft_contract()
    rendered = PromptRenderer.render(get_role("architecture_planner"), inputs={"planning_index": index, "delivery_constraints": constraints, "repair_context": None}, output_schema=schema, output_example=example)
    assert lineage["lineage_id"]
    assert "orbitnet" in json.dumps(constraints)
    assert "OrbitNet" in rendered.user
