import json
import hashlib
import pathlib
import threading

import pytest

from nepa.calibration.s4_architecture import ArchitectureCalibrationDriver, CalibrationBatchDeclaration, CalibrationDeclarationError, CalibrationEvidenceError, recompute_calibration_report
from nepa.config import load_config
from nepa.llm.client import LLMResponse, ParameterSupportState, TransportError


class _Provider:
    native_structured_output = False

    def complete(self, request, *, model, native_schema):
        return LLMResponse(text="{}", tokens_in=1, tokens_out=1, cost_usd=0, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


class _FormatRepairProvider:
    native_structured_output = False

    def __init__(self):
        self.calls = 0

    def complete(self, request, *, model, native_schema):
        self.calls += 1
        text = "not-json" if self.calls == 1 else pathlib.Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8")
        return LLMResponse(text=text, tokens_in=2, tokens_out=3, cost_usd=0.01, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


class _SemanticRepairProvider:
    native_structured_output = False

    def complete(self, request, *, model, native_schema):
        return LLMResponse(text=pathlib.Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"), tokens_in=4, tokens_out=5, cost_usd=0.02, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


class _BarrierProvider:
    native_structured_output = False

    def __init__(self, barrier):
        self.barrier = barrier

    def complete(self, request, *, model, native_schema):
        self.barrier.wait(timeout=5)
        return LLMResponse(text="{}", tokens_in=1, tokens_out=1, cost_usd=0, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


class _TransportFailureProvider:
    native_structured_output = False

    def complete(self, request, *, model, native_schema):
        raise TransportError("fixture transport exhausted", provider="fixture")


class _TwoSemanticRepairGateRegressionProvider:
    native_structured_output = False

    def __init__(self):
        self.calls = 0

    def complete(self, request, *, model, native_schema):
        draft = json.loads(pathlib.Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"))
        if self.calls == 0:
            draft["modules"][0]["provides_contracts"] = ["unknown-contract"]
        elif self.calls == 2:
            draft["modules"][0]["provides_contracts"] = []
        self.calls += 1
        return LLMResponse(text=json.dumps(draft), tokens_in=4, tokens_out=5, cost_usd=0.02, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


class _VersionedFormatRepairProvider:
    native_structured_output = False

    def __init__(self):
        self.calls = 0

    def complete(self, request, *, model, native_schema):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="not-json", tokens_in=7, tokens_out=8, cost_usd=0.03,
                model="model-v1", provider_metadata={"finish_reason": "length"},
                parameter_support={"temperature": ParameterSupportState.UNKNOWN},
            )
        return LLMResponse(
            text=pathlib.Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"),
            tokens_in=9, tokens_out=10, cost_usd=0.04, model="model-v2",
            provider_metadata={"finish_reason": "stop"},
            parameter_support={"temperature": ParameterSupportState.REPORTED_APPLIED},
        )


class _RetryingProvider:
    native_structured_output = False

    def __init__(self):
        self.calls = 0

    def complete(self, request, *, model, native_schema):
        self.calls += 1
        if self.calls < 4:
            raise TransportError("retryable fixture transport", provider="fixture")
        return LLMResponse(
            text=pathlib.Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"),
            tokens_in=11, tokens_out=12, cost_usd=0.05, model=model,
            provider_metadata={"finish_reason": "stop"},
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


class _SessionProvider(_Provider):
    def __init__(self, session):
        self.session = session


def _fixture_config():
    return load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}}, "calibration_models": {name: {"provider": "fixture", "model": "model", "temperature": 0, "max_tokens": 100} for name in ("claude", "qwen", "deepseek")}})


def _declaration(**kwargs):
    values = {"trial_count": 1, "semantic_repair_depth": 0, "context_window_tokens": {name: 100000 for name in ("claude", "qwen", "deepseek")}, "spec": "gold_file/specIR.json", "target_profile": "gold_file/target.json", "test_bundle": "gold_file/test_bundle.json"}
    values.update(kwargs)
    return CalibrationBatchDeclaration(**values)


def test_calibration_batch_isolated_and_not_a_formal_run(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration())
    roots = list(tmp_path.glob("_calibration/s4-architecture/*/v0/*"))
    assert {path.name for path in roots} == {"claude", "qwen", "deepseek"}
    assert not list(tmp_path.rglob("run.json"))

    report = json.loads((roots[0] / "calibration_report.json").read_text(encoding="utf-8"))
    assert report["usage"]["calls"] == 2
    assert report["usage"]["tokens_in"] == 2
    assert report["repairs"]["format"] == 1
    assert report["repairs"]["format_usage"]["calls"] == 2


def test_request_response_indexes_bind_format_repair_evidence(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _FormatRepairProvider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    request = json.loads((root / "trials/trial_001/request_ref.json").read_text(encoding="utf-8"))
    response = json.loads((root / "trials/trial_001/response_ref.json").read_text(encoding="utf-8"))
    validation = json.loads((root / "trials/trial_001/validation.json").read_text(encoding="utf-8"))
    trace = json.loads(next((root / "trace/trials").glob("trial_001_p0_*.json")).read_text(encoding="utf-8"))
    assert len(request["attempts"][0]["request_evidence"]) == 2
    assert len(response["attempts"][0]["response_evidence"]) == 2
    assert validation["attempts"][0]["request_refs"] == request["attempts"][0]["request_evidence"]
    assert validation["attempts"][0]["response_refs"] == response["attempts"][0]["response_evidence"]
    assert request["attempts"][0]["prompt_sha256"] == trace["prompt_template_sha256"]
    assert recompute_calibration_report(root)["trial_metrics"][0]["schema_after_format_repair"] is True


def test_report_rebuilds_each_format_call_metric_and_model_version(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _VersionedFormatRepairProvider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    report = recompute_calibration_report(root)
    assert report["usage"]["calls"] == 2
    assert report["usage"]["tokens_in"] == 16
    assert report["usage"]["tokens_out"] == 18
    assert report["usage"]["truncated"] == 1
    assert set(report["model_identity"]["versions"]) == {"model-v1", "model-v2"}
    assert report["trial_metrics"][0]["usage"]["calls"] == 2


def test_report_counts_transport_retries_from_output_evidence(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _RetryingProvider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    report = recompute_calibration_report(root)
    assert report["usage"]["calls"] == 4
    assert report["usage"]["tokens_in"] == 11
    validation = json.loads((root / "trials/trial_001/validation.json").read_text(encoding="utf-8"))
    assert validation["attempts"][0]["call_metrics"][0]["transport_attempts"] == 4


def test_semantic_repair_is_a_separate_attempt_with_bounded_evidence(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _SemanticRepairProvider()}).run(_declaration(semantic_repair_depth=1))
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    validation = json.loads((root / "trials/trial_001/validation.json").read_text(encoding="utf-8"))
    request = json.loads((root / "trials/trial_001/request_ref.json").read_text(encoding="utf-8"))
    assert [attempt["depth"] for attempt in validation["attempts"]] == [0, 1]
    assert request["attempts"][1]["kind"] == "semantic_repair"
    assert len(validation["attempts"]) == 2
    assert json.loads((root / "calibration_report.json").read_text(encoding="utf-8"))["repairs"]["semantic"]["p1"] == 1


def test_three_workers_overlap_but_keep_model_roots_separate(tmp_path):
    barrier = threading.Barrier(3)
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _BarrierProvider(barrier)}).run(_declaration())
    roots = list(tmp_path.glob("_calibration/s4-architecture/*/v0/*"))
    assert {path.name for path in roots} == {"claude", "qwen", "deepseek"}
    for root in roots:
        traces = [json.loads(line) for line in (root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines() if line]
        assert traces and all(trace["run_id"].endswith(root.name) for trace in traces)
        assert all(root.name not in trace.get("provider_prompt_paths", []) for trace in traces)


def test_infrastructure_invalid_worker_does_not_publish_a_report(tmp_path):
    def providers(model_id, *args):
        return {"fixture": _TransportFailureProvider()} if model_id == "claude" else {"fixture": _Provider()}

    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=providers).run(_declaration())
    assert not list(tmp_path.glob("_calibration/s4-architecture/*/v0/claude/calibration_report.json"))
    assert list(tmp_path.glob("_calibration/s4-architecture/*/v0/qwen/calibration_report.json"))


def test_declared_prompt_hash_cannot_override_template_hash(tmp_path):
    with pytest.raises(CalibrationDeclarationError, match="prompt_sha256"):
        ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration(prompt_sha256="0" * 64))


@pytest.mark.parametrize("prompt_version", ["../escaped", "/absolute", "a/b", r"a\\b", ".", ".."])
def test_prompt_version_is_one_safe_path_segment(prompt_version):
    with pytest.raises(CalibrationDeclarationError, match="safe non-empty single path segment"):
        _declaration(prompt_version=prompt_version).targets(_fixture_config())


def test_reloaded_model_root_and_template_bytes_are_bound(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    batch_path = root / "batch.json"
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch["prompt_version"] = "v1"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(CalibrationEvidenceError, match="model root directory"):
        recompute_calibration_report(root)

    batch["prompt_version"] = "v0"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    template_path = root / "prompt/template.md"
    template_path.write_bytes(template_path.read_bytes() + b"\n tampered")
    with pytest.raises(CalibrationEvidenceError, match="hash mismatch"):
        recompute_calibration_report(root)


def test_prepared_live_mapping_mutation_cannot_change_frozen_lineage_inputs(tmp_path):
    from nepa.speclib.planning import prepare_architecture_inputs

    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    original_protocol = prepared.spec["protocol"]["name"]
    declaration = _declaration(prepared_inputs=prepared)
    prepared.spec["protocol"]["name"] = "mutated-after-prepare"
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(declaration)
    lineage_root = next(tmp_path.glob("_calibration/s4-architecture/*"))
    frozen_spec = json.loads((lineage_root / "inputs/spec.json").read_text(encoding="utf-8"))
    planning = json.loads((lineage_root / "planning_index.json").read_text(encoding="utf-8"))
    assert frozen_spec["protocol"]["name"] == original_protocol
    assert planning["protocol"]["name"] == original_protocol


def test_reloaded_lineage_requires_closed_controlled_fields(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    lineage_path = root.parents[1] / "lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    lineage.pop("components")
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
    with pytest.raises(CalibrationEvidenceError, match="invalid lineage manifest"):
        recompute_calibration_report(root)


def test_crash_before_trial_rename_requires_a_fresh_attempt_root(tmp_path):
    fired = {"value": False}

    def crash_once(event):
        if event == "trial_before_rename" and not fired["value"]:
            fired["value"] = True
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration(fault_hook=crash_once))
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration(attempt=2))
    assert list(tmp_path.glob("_calibration/s4-architecture/*/v0/attempt_002/*/calibration_report.json"))
    assert not list(tmp_path.rglob("run.json"))


def test_request_ref_drift_is_rejected(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    request_path = root / "trials/trial_001/request_ref.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["attempts"][0]["request"]["sha256"] = "0" * 64
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(Exception, match="hash mismatch|reference mismatch"):
        recompute_calibration_report(root)


def test_two_semantic_repairs_record_gate_regression_and_final_candidate(tmp_path):
    ArchitectureCalibrationDriver(
        _fixture_config(),
        runs_root=tmp_path,
        provider_factory=lambda *args: {"fixture": _TwoSemanticRepairGateRegressionProvider()},
    ).run(_declaration(semantic_repair_depth=2))
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    report = json.loads((root / "calibration_report.json").read_text(encoding="utf-8"))
    validation = json.loads((root / "trials/trial_001/validation.json").read_text(encoding="utf-8"))
    assert [attempt["depth"] for attempt in validation["attempts"]] == [0, 1, 2]
    assert report["gates"]["arch_05"]["passed"] == 0
    assert report["gate_stages"]["arch_05"]["p0"]["passed"] == 0
    assert report["gate_stages"]["arch_05"]["p1"]["passed"] == 1
    assert report["gate_stages"]["arch_05"]["p2"]["passed"] == 0
    assert report["repairs"]["stage_gain"]["arch_05"]["p0_to_p1"]["gain"] == 1
    assert report["repairs"]["stage_gain"]["arch_05"]["p1_to_p2"]["gain"] == -1
    assert report["trial_metrics"][0]["gate_changes"]["arch_05"] == {"p0_to_p1": "improved", "p1_to_p2": "regressed"}


def test_coordinated_validation_summary_tamper_is_recomputed_from_candidate(tmp_path):
    ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _SemanticRepairProvider()}).run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/claude"))
    validation_path = root / "validations/trial_001_p0.json"
    recorded = json.loads(validation_path.read_text(encoding="utf-8"))
    recorded["issues"][0]["message"] = "coordinated summary tamper"
    validation_bytes = json.dumps(recorded, separators=(",", ":")).encode("utf-8")
    validation_path.write_bytes(validation_bytes)

    trial_validation_path = root / "trials/trial_001/validation.json"
    trial_validation = json.loads(trial_validation_path.read_text(encoding="utf-8"))
    trial_validation["attempts"][0]["validation_ref"]["sha256"] = hashlib.sha256(validation_bytes).hexdigest()
    trial_validation_bytes = json.dumps(trial_validation, separators=(",", ":")).encode("utf-8")
    trial_validation_path.write_bytes(trial_validation_bytes)
    response_path = root / "trials/trial_001/response_ref.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["validation"]["sha256"] = hashlib.sha256(trial_validation_bytes).hexdigest()
    response_path.write_text(json.dumps(response, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(Exception, match="deterministic ARCH_VALIDATE"):
        recompute_calibration_report(root)


def test_shared_provider_mapping_and_adapter_instances_are_rejected(tmp_path):
    with pytest.raises(CalibrationDeclarationError, match="provider mappings"):
        ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path / "mapping", provider_factory={"fixture": _Provider()}).run(_declaration())

    shared = _Provider()
    with pytest.raises(CalibrationDeclarationError, match="shared between calibration workers"):
        ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path / "shared", provider_factory=lambda *args: {"fixture": shared}).run(_declaration())

    shared_session = object()
    with pytest.raises(CalibrationDeclarationError, match="provider session is shared between calibration workers"):
        ArchitectureCalibrationDriver(_fixture_config(), runs_root=tmp_path / "session", provider_factory=lambda *args: {"fixture": _SessionProvider(shared_session)}).run(_declaration())
