import copy

import pytest

import nepa.calibration.s4_prompt_development as development
from nepa.calibration.s4_prompt_development import _combine_reports


def _report(start: int):
    trials = [f"trial_{index:03d}" for index in range(start, start + 5)]
    trial_metrics = []
    gates = {f"arch_{index:02d}": {"passed": 5, "denominator": 5, "rate": 1.0} for index in range(1, 11)}
    gate_stages = {gate: {"p0": {"passed": 5, "denominator": 5, "rate": 1.0}, "p1": {"passed": 5, "denominator": 5, "rate": 1.0}, "p2": None} for gate in gates}
    for trial in trials:
        trial_metrics.append({
            "trial_id": trial, "terminal": "pass", "first_passing_depth": 0,
            "schema_first_pass": True, "schema_after_format_repair": True, "semantic_first_pass": True,
            "p0": True, "p1": True, "p2": None,
            "gates": {gate: {"initial_passed": True, "final_passed": True} for gate in gates},
            "gate_stages": {gate: {"p0": True, "p1": True, "p2": None} for gate in gates},
            "gate_changes": {gate: {"p0_to_p1": "unchanged", "p1_to_p2": "not_declared"} for gate in gates},
            "usage": {"calls": 1, "tokens_in": 1, "tokens_out": 1, "cost_usd": 1.0, "latency_ms": 1, "finish_reasons": {"stop": 1}, "truncated": 0},
            "repairs": {"format": 0, "format_usage": {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0, "latency_ms": 0, "finish_reasons": {}, "truncated": 0}, "semantic": {"p1": 0, "p2": 0}, "semantic_usage": {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0, "latency_ms": 0, "finish_reasons": {}, "truncated": 0}},
        })
    usage = {"calls": 5, "tokens_in": 5, "tokens_out": 5, "cost_usd": 5.0, "latency_ms": 5, "finish_reasons": {"stop": 5}, "truncated": 0}
    repairs = {"format": 0, "format_usage": copy.deepcopy(usage), "semantic": {"p1": 0, "p2": 0}, "semantic_usage": copy.deepcopy(usage), "gain": {gate: {"initial_passed": 5, "final_passed": 5, "denominator": 5, "initial_rate": 1.0, "final_rate": 1.0, "gain": 0.0} for gate in gates}, "stage_gain": {gate: {"p0_to_p1": None, "p1_to_p2": None} for gate in gates}}
    return {"lineage_id": "d" * 64, "prompt_version": "v1", "prompt_sha256": "e" * 64, "model_id": "qwen", "trial_count": 5, "status": "complete", "metrics": {"schema_first_pass_rate": 1.0, "schema_after_format_repair_rate": 1.0, "arch_raw_first_pass_rate": 1.0, "arch_semantic_first_pass_rate": 1.0, "p0": 1.0, "p1": 1.0, "p2": None, "p1_reason": None, "p2_reason": {"code": "SEMANTIC_DEPTH_NOT_DECLARED", "message": "p2 is not declared for this batch"}}, "gates": gates, "gate_stages": gate_stages, "failure_cooccurrence": {left: {right: 0 for right in gates} for left in gates}, "repairs": repairs, "usage": usage, "model_identity": {"provider": "provider", "model": "model", "versions": ["version"], "parameter_support": {"temperature": ["unknown"]}}, "trials": trials, "trial_metrics": trial_metrics}


def test_extension_combines_exactly_five_plus_five_and_recomputes_usage():
    base = _report(1)
    extension = copy.deepcopy(base)
    extension["trials"] = [f"trial_{index:03d}" for index in range(6, 11)]
    for index, item in enumerate(extension["trial_metrics"], start=6):
        item["trial_id"] = f"trial_{index:03d}"
    combined = _combine_reports(base, extension)
    assert combined["trial_count"] == 10
    assert combined["trials"] == [f"trial_{index:03d}" for index in range(1, 11)]
    assert combined["usage"]["calls"] == 10
    assert combined["metrics"]["p1"] == 1.0


def test_extension_recompute_failure_publishes_invalid_attempt(tmp_path, monkeypatch):
    root = tmp_path / ("a" * 64)
    root.mkdir()
    coordinator = object.__new__(development.PromptDevelopmentCoordinator)
    coordinator.root = root
    coordinator.provider_factory = None

    monkeypatch.setattr(coordinator, "_selection_exists", lambda: False)
    monkeypatch.setattr(
        coordinator,
        "_preflight",
        lambda: type("Preflight", (), {"config": object(), "context_limits": {model_id: 4096 for model_id in development.MODEL_IDS}})(),
    )
    monkeypatch.setattr(coordinator, "_next_attempt", lambda version, extension=False: 1)
    monkeypatch.setattr(coordinator, "_check_source_snapshot", lambda version_record, prompt: None)
    monkeypatch.setattr(coordinator, "_source_guard", lambda prompt: lambda: None)
    monkeypatch.setattr(coordinator, "_frozen_batch_inputs", lambda lineage: (b"spec", b"target", b"bundle"))
    monkeypatch.setattr(
        development,
        "_load_protocol",
        lambda root, preflight=None: ({}, {"lineage_id": root.name}),
    )
    monkeypatch.setattr(
        development,
        "_load_assessment",
        lambda root, version, count: {
            "status": "complete",
            "ambiguity": "metric_conflict",
            "attempt": 1,
            "models": {model_id: {"report_ref": {"path": "report.json", "sha256": "b" * 64}} for model_id in development.MODEL_IDS},
        },
    )
    monkeypatch.setattr(
        development,
        "_load_version",
        lambda root, version: {
            "prompt_ref": {"path": "prompt-development/versions/v1/prompt.md", "sha256": "a" * 64},
            "prompt_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(development, "_verify_root_ref", lambda root, value, label: b"prompt")

    published = []

    def fake_publish_json(root, relative, value, schema_key):
        published.append((relative, value, schema_key))
        return {"path": relative, "sha256": "c" * 64}

    monkeypatch.setattr(development, "_publish_json", fake_publish_json)

    class FakeDriver:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, batch):
            return None

    monkeypatch.setattr(development, "ArchitectureCalibrationDriver", FakeDriver)

    def recompute(path, config):
        if "extensions/n010" in path.as_posix():
            raise development.CalibrationEvidenceError("tampered extension evidence")
        return {"status": "complete"}

    monkeypatch.setattr(development, "recompute_calibration_report", recompute)

    with pytest.raises(development.PromptDevelopmentError, match="N10 extension attempt 1 failed"):
        coordinator.expand("v1")

    outcomes = [value for relative, value, schema_key in published if relative.endswith("attempt_001/outcome.json")]
    assert outcomes and outcomes[-1]["status"] == "infrastructure-invalid"
    assert not (root / "prompt-development/versions/v1/assessment-n010.json").exists()
