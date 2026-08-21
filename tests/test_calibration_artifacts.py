import json

import pytest

from nepa.calibration.s4_architecture import ArchitectureCalibrationDriver, CalibrationBatchDeclaration, CalibrationEvidenceError, build_lineage_manifest
from nepa.config import load_config
from nepa.llm.client import LLMResponse, ParameterSupportState


class _Provider:
    native_structured_output = False

    def __init__(self, calls):
        self.calls = calls

    def complete(self, request, *, model, native_schema):
        self.calls.append(model)
        return LLMResponse(text="{}", tokens_in=1, tokens_out=1, cost_usd=0, model=model, parameter_support={"temperature": ParameterSupportState.UNKNOWN})


def _config():
    return load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}}, "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}}, "calibration_models": {name: {"provider": "fixture", "model": "model", "temperature": 0, "max_tokens": 65536} for name in ("qwen", "deepseek")}})


def _declaration(**kwargs):
    values = {
        "trial_count": 1,
        "semantic_repair_depth": 0,
        "context_window_tokens": {name: 100000 for name in ("qwen", "deepseek")},
        "spec": "gold_file/specIR.json",
        "target_profile": "gold_file/target.json",
        "test_bundle": "gold_file/test_bundle.json",
    }
    values.update(kwargs)
    return CalibrationBatchDeclaration(**values)


def test_calibration_artifact_contract_is_named_by_committed_trial_files():
    from pathlib import Path
    assert {"request_ref.json", "response_ref.json", "validation.json"} == {"request_ref.json", "response_ref.json", "validation.json"}
    assert Path("nepa/schemas/trial-validation.schema.json").is_file()


def test_n5_n10_and_depth_variants_share_the_same_lineage_identity(tmp_path):
    config = _config()
    driver = ArchitectureCalibrationDriver(config, runs_root=tmp_path)
    lineage_ids = []
    for declaration in (
        _declaration(trial_count=5, semantic_repair_depth=1, context_window_tokens={name: 128000 for name in ("qwen", "deepseek")}),
        _declaration(trial_count=10, semantic_repair_depth=2, prompt_version="v1", context_window_tokens={name: 128000 for name in ("qwen", "deepseek")}),
    ):
        prepared, planning, manifest, constraints = driver._prepare(declaration)
        targets = declaration.targets(config)
        lineage = build_lineage_manifest(
            prepared,
            planning,
            manifest,
            constraints,
            config=config,
            model_targets=targets,
            context_window_tokens={model_id: target.context_window_tokens for model_id, target in targets.items()},
        )
        lineage_ids.append(lineage["lineage_id"])
    assert lineage_ids[0] == lineage_ids[1]


def test_committed_trials_are_verified_and_reused_while_orphan_trace_is_ignored(tmp_path):
    first_calls = []
    driver = ArchitectureCalibrationDriver(_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider(first_calls)})
    driver.run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/qwen"))
    report_bytes = (root / "calibration_report.json").read_bytes()
    trace_path = root / "trace/llm_calls.ndjson"
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"task_id": "trial_001", "orphan": True}) + "\n")
    second_calls = []
    ArchitectureCalibrationDriver(_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider(second_calls)}).run(_declaration())
    assert not second_calls
    assert (root / "calibration_report.json").read_bytes() == report_bytes


def test_incomplete_committed_trial_is_not_reused(tmp_path):
    driver = ArchitectureCalibrationDriver(_config(), runs_root=tmp_path, provider_factory=lambda *args: {"fixture": _Provider([])})
    driver.run(_declaration())
    root = next(tmp_path.glob("_calibration/s4-architecture/*/v0/qwen"))
    (root / "trials/trial_001/response_ref.json").unlink()
    with pytest.raises(CalibrationEvidenceError, match="incomplete trial"):
        driver.run(_declaration())


def test_after_rename_crash_reuses_the_committed_trial(tmp_path):
    fired = {"value": False}

    def crash_once(event):
        if event == "trial_after_rename" and not fired["value"]:
            fired["value"] = True
            raise RuntimeError("simulated post-commit crash")

    with pytest.raises(RuntimeError, match="post-commit crash"):
        ArchitectureCalibrationDriver(
            _config(), runs_root=tmp_path,
            provider_factory=lambda *args: {"fixture": _Provider([])},
        ).run(_declaration(fault_hook=crash_once))

    second_calls = []
    ArchitectureCalibrationDriver(
        _config(), runs_root=tmp_path,
        provider_factory=lambda *args: {"fixture": _Provider(second_calls)},
    ).run(_declaration())
    assert not second_calls
    assert len(list(tmp_path.glob("_calibration/s4-architecture/*/v0/*/calibration_report.json"))) == 2
