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
        Path("nepa/assets/layout_conventions/c99-server-v1.json"),
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
    draft = json.loads((root / "architecture-draft.json").read_text(encoding="utf-8"))
    validation = validate_architecture(draft, index, manifest, constraints)
    assert validation["verdict"] == "pass"
    targets = {name: {"provider": "fixture", "model": name, "temperature": 0, "max_tokens": 65536, "context_window_tokens": 10000} for name in ("qwen", "claude", "deepseek")}
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {f"fixture/{name}": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1} for name in ("qwen", "claude", "deepseek")}}})
    lineage = build_lineage_manifest(prepared, index, manifest, constraints, config=config, model_targets=targets)
    schema, example = architecture_draft_contract()
    rendered = PromptRenderer.render(get_role("architecture_planner"), inputs={"planning_index": index, "delivery_constraints": constraints, "repair_context": None}, output_schema=schema, output_example=example)
    assert lineage["lineage_id"]
    assert "orbitnet" in json.dumps(constraints)
    assert "OrbitNet" in rendered.user
