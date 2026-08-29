import hashlib
import json

import pytest
from jsonschema import Draft202012Validator

from nepa.calibration.s4_architecture import ArchitectureCalibrationDriver, CalibrationBatchDeclaration, CalibrationEvidenceError, recompute_calibration_report
from nepa.config import load_config


def test_calibration_report_schema_has_fixed_gate_denominators():
    schema = json.load(open("nepa/schemas/calibration-report.schema.json", encoding="utf-8"))
    assert set(schema["properties"]["gates"]["required"]) == {f"arch_{index:02d}" for index in range(1, 16)}
    Draft202012Validator.check_schema(schema)


def _config():
    return load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}}, "calibration_models": {name: {"provider": "fixture", "model": "model", "temperature": 0, "max_tokens": 65536} for name in ("qwen", "claude", "deepseek")}})


class _SchemaFailProvider:
    native_structured_output = False

    def complete(self, request, *, model, native_schema):
        from nepa.llm.client import LLMResponse, ParameterSupportState
        return LLMResponse(text="{}", tokens_in=1, tokens_out=1, cost_usd=0, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


def _batch(tmp_path):
    declaration = CalibrationBatchDeclaration(trial_count=2, semantic_repair_depth=0, context_window_tokens={name: 100000 for name in ("qwen", "claude", "deepseek")}, spec="gold_file/specIR.json", target_profile="gold_file/target.json", test_bundle="gold_file/test_bundle.json")
    ArchitectureCalibrationDriver(_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _SchemaFailProvider()}).run(declaration)
    return next(tmp_path.glob("_calibration/s4-architecture/*/v0/qwen"))


def test_report_uses_hand_calculated_N_and_recomputation_is_identical(tmp_path):
    root = _batch(tmp_path)
    first = recompute_calibration_report(root)
    second = recompute_calibration_report(root)
    assert first == second
    assert first["trial_count"] == 2
    assert all(value["denominator"] == 2 for value in first["gates"].values())
    assert all(value["denominator"] == 2 for value in first["repairs"]["gain"].values())
    assert len(first["trial_metrics"]) == 2
    assert first["usage"]["calls"] == 4
    assert first["metrics"]["p2"] is None
    assert first["metrics"]["p2_reason"]["code"] == "SEMANTIC_DEPTH_NOT_DECLARED"


def test_parent_artifact_drift_is_rejected_without_overwrite(tmp_path):
    root = _batch(tmp_path)
    lineage_root = root.parents[1]
    path = lineage_root / "inputs/spec.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(CalibrationEvidenceError, match="hash mismatch"):
        recompute_calibration_report(root)


def test_controlled_component_drift_is_rejected(tmp_path):
    root = _batch(tmp_path)
    component = root.parents[1] / "components/planning.bundle.json"
    component.write_bytes(component.read_bytes() + b"\ncomponent drift")
    with pytest.raises(CalibrationEvidenceError, match="hash mismatch|controlled component drift"):
        recompute_calibration_report(root)


def test_current_controlled_source_drift_is_rejected(tmp_path, monkeypatch):
    root = _batch(tmp_path)
    from nepa.calibration import s4_architecture

    original = s4_architecture._default_components

    def drifted_components():
        values = original()
        values["llm_runtime"] += b"\ncurrent source drift"
        return values

    monkeypatch.setattr(s4_architecture, "_default_components", drifted_components)
    with pytest.raises(CalibrationEvidenceError, match="controlled component drift"):
        recompute_calibration_report(root)


def test_recorded_semantic_depth_and_validation_parent_must_match_batch(tmp_path):
    root = _batch(tmp_path)
    batch_path = root / "batch.json"
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch["semantic_depth"] = 1
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(CalibrationEvidenceError, match="semantic repair depth"):
        recompute_calibration_report(root)


def test_duplicate_request_evidence_is_rejected_even_when_hashes_are_valid(tmp_path):
    root = _batch(tmp_path)
    request_path = root / "trials/trial_001/request_ref.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    duplicate = request["attempts"][0]["request_evidence"][0]
    request["attempts"][0]["request_evidence"].append(duplicate)
    validation_path = root / "trials/trial_001/validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["attempts"][0]["request_refs"].append(duplicate)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    validation_bytes = json.dumps(validation).encode("utf-8")
    validation_path.write_bytes(validation_bytes)
    response_path = root / "trials/trial_001/response_ref.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["validation"]["sha256"] = hashlib.sha256(validation_bytes).hexdigest()
    response_path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(CalibrationEvidenceError, match="duplicate request/response evidence"):
        recompute_calibration_report(root)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lineage_id", "0" * 64, "lineage id"),
        ("trial_count", 1, "trial count"),
        ("trials", ["trial_001"], "trial ids"),
        ("provider", "drifted-provider", "batch provider"),
            ("model", "drifted-model", "trace identity"),
        ("context_window_tokens", 1, "batch context_window_tokens"),
    ],
)
def test_batch_controlled_fields_cannot_drift_from_lineage(tmp_path, field, value, message):
    root = _batch(tmp_path)
    batch_path = root / "batch.json"
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch[field] = value
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(CalibrationEvidenceError, match=message):
        recompute_calibration_report(root)
