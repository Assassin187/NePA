import json
from pathlib import Path

import pytest

from nepa.calibration.s4_architecture import CalibrationEvidenceError
from nepa.calibration.s4_prompt_development import (
    PromptDevelopmentConfigError,
    PromptDevelopmentCoordinator,
    PromptDevelopmentError,
    PromptDevelopmentEvidenceError,
    validate_development_report,
    write_development_report,
    preflight_calibration_config,
    scan_prompt_neutrality,
)
from nepa.llm.client import LLMResponse, ParameterSupportState


def _config_files(tmp_path: Path):
    config = tmp_path / "calibration.yaml"
    config.write_text(
        """
providers:
  qwen-provider: {kind: openai_compat, base_url: https://qwen.invalid, api_key_env: NEPA_QWEN_API_KEY}
  claude-provider: {kind: anthropic, base_url: https://claude.invalid, api_key_env: NEPA_CLAUDE_API_KEY}
  deepseek-provider: {kind: openai_compat, base_url: https://deepseek.invalid, api_key_env: NEPA_DS_API_KEY}
calibration_models:
  qwen: {provider: qwen-provider, model: model-qwen, temperature: 0.0, max_tokens: 65536}
  claude: {provider: claude-provider, model: model-claude, temperature: 0.0, max_tokens: 65536}
  deepseek: {provider: deepseek-provider, model: model-deepseek, temperature: 0.0, max_tokens: 65536}
pricing:
  models:
    qwen-provider/model-qwen: {input_usd_per_million_tokens: 1, output_usd_per_million_tokens: 1}
    claude-provider/model-claude: {input_usd_per_million_tokens: 1, output_usd_per_million_tokens: 1}
    deepseek-provider/model-deepseek: {input_usd_per_million_tokens: 1, output_usd_per_million_tokens: 1}
""",
        encoding="utf-8",
    )
    limits = tmp_path / "limits.json"
    # The exact-algorithm prompt and a full repair context require slightly
    # more than the former synthetic 100k fixture window.
    limits.write_text(json.dumps({"qwen": 110000, "claude": 110000, "deepseek": 110000}), encoding="utf-8")
    return config, limits


class _Provider:
    native_structured_output = False

    def __init__(self):
        self.calls = 0

    def complete(self, request, *, model, native_schema):
        self.calls += 1
        example = Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8")
        return LLMResponse(
            text=example,
            tokens_in=3,
            tokens_out=4,
            cost_usd=0.01,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


def _factory(instances):
    def factory(model_id, target, *_args):
        provider = instances.setdefault(model_id, _Provider())
        return {target.provider: provider}
    return factory


def test_config_preflight_rejects_missing_pricing_and_requires_explicit_limits(tmp_path):
    config, limits = _config_files(tmp_path)
    preflight = preflight_calibration_config(config, limits, require_environment=False)
    assert set(preflight.context_limits) == {"qwen", "claude", "deepseek"}
    broken = config.read_text(encoding="utf-8").replace("deepseek-provider/model-deepseek:", "missing/model:")
    config.write_text(broken, encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="missing pricing"):
        preflight_calibration_config(config, limits, require_environment=False)


def test_neutrality_rejects_protocol_and_model_specific_source():
    with pytest.raises(PromptDevelopmentError, match="neutrality"):
        scan_prompt_neutrality("Use MQTT and model-qwen in the prompt")
    scan_prompt_neutrality("Use only the supplied delimited artifacts and generic contracts")


def test_init_binds_corrected_lineage_and_snapshot_then_runs_three_isolated_models(tmp_path):
    config, limits = _config_files(tmp_path)
    instances = {}
    coordinator = PromptDevelopmentCoordinator.init(
        config_path=config,
        context_limits_path=limits,
        runs_root=tmp_path / "runs",
        provider_factory=_factory(instances),
        require_environment=False,
    )
    protocol = json.loads((coordinator.root / "prompt-development/protocol.json").read_text(encoding="utf-8"))
    lineage = json.loads((coordinator.root / "lineage.json").read_text(encoding="utf-8"))
    assert protocol["lineage_id"] == lineage["lineage_id"]
    assert "trial_count" not in lineage["statistics"]
    assert "semantic_depth" not in lineage["statistics"]
    result = coordinator.run_version("v0")
    assert result["assessment"]["trial_count"] == 5
    assert result["assessment"]["attempt"] == 1
    assert result["assessment"]["screening_pass"] is False
    assert set(instances) == {"qwen", "claude", "deepseek"}
    assert (coordinator.root / "prompt-development/versions/v0/prompt.md").read_bytes() == Path("nepa/agents/prompts/architecture_planner.md").read_bytes()
    summary = write_development_report(
        coordinator.root,
        config_path=config,
        context_limits_path=limits,
        output_dir=tmp_path / "results",
    )
    assert set(summary["versions"]["v0"]["slots"]) == {"qwen", "claude", "deepseek"}
    report_path = tmp_path / "results/development-report.md"
    validate_development_report(summary, report_path.read_text(encoding="utf-8"))
    for model_id in ("qwen", "claude", "deepseek"):
        value = json.loads((coordinator.root / "v0" / model_id / "batch.json").read_text(encoding="utf-8"))
        assert value["trial_count"] == 5
        assert value["semantic_depth"] == 1
        assert value["prompt_sha256"] == protocol_prompt_hash(coordinator.root)


def protocol_prompt_hash(root: Path) -> str:
    import hashlib
    return hashlib.sha256((root / "prompt-development/versions/v0/prompt.md").read_bytes()).hexdigest()


def test_snapshot_mutation_and_selection_block_provider_work(tmp_path):
    config, limits = _config_files(tmp_path)
    instances = {}
    coordinator = PromptDevelopmentCoordinator.init(config_path=config, context_limits_path=limits, runs_root=tmp_path / "runs", provider_factory=_factory(instances), require_environment=False)
    snapshot = coordinator.root / "prompt-development/versions/v0/prompt.md"
    original = snapshot.read_bytes()
    snapshot.write_bytes(original + b"\n")
    with pytest.raises(PromptDevelopmentEvidenceError):
        coordinator.run_version("v0")


def test_fixed_key_mapping_is_checked_without_reading_values(tmp_path, monkeypatch):
    config, limits = _config_files(tmp_path)
    monkeypatch.delenv("NEPA_QWEN_API_KEY", raising=False)
    with pytest.raises(PromptDevelopmentConfigError, match="NEPA_QWEN_API_KEY"):
        preflight_calibration_config(config, limits, require_environment=True)


def test_revision_requires_complete_prior_failure_and_binds_one_prompt_diff(tmp_path):
    config, limits = _config_files(tmp_path)
    source = tmp_path / "architecture_planner.md"
    source.write_bytes(Path("nepa/agents/prompts/architecture_planner.md").read_bytes())
    instances = {}
    coordinator = PromptDevelopmentCoordinator.init(
        config_path=config, context_limits_path=limits, runs_root=tmp_path / "runs",
        provider_factory=_factory(instances), prompt_source_path=source, require_environment=False,
    )
    result = coordinator.run_version("v0")
    assert result["assessment"]["screening_pass"] is False
    evidence = {"path": "prompt-development/versions/v0/assessment-n005.json", "sha256": __import__("hashlib").sha256((coordinator.root / "prompt-development/versions/v0/assessment-n005.json").read_bytes()).hexdigest()}
    source.write_bytes(source.read_bytes() + b"\nUse a final generic consistency checklist.\n")
    revision = coordinator.record_revision("v1", hypothesis="The self-check order is underspecified.", evidence_refs=[evidence], expected_gates=["arch_07"])
    assert revision["version"] == "v1"
    assert (coordinator.root / "prompt-development/versions/v1/revision.json").is_file()
    v1_result = coordinator.run_version("v1")
    assert v1_result["assessment"]["status"] == "complete"
    v1_evidence_path = coordinator.root / "prompt-development/versions/v1/assessment-n005.json"
    v1_evidence = {"path": "prompt-development/versions/v1/assessment-n005.json", "sha256": __import__("hashlib").sha256(v1_evidence_path.read_bytes()).hexdigest()}
    with pytest.raises(PromptDevelopmentError, match="distinct"):
        coordinator.record_revision("v2", hypothesis="The self-check order is underspecified.", evidence_refs=[v1_evidence], prompt_bytes=source.read_bytes())
