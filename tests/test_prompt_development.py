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
  selected-provider: {kind: anthropic, base_url: https://selected.invalid, api_key_env: NEPA_SELECTED_API_KEY}
calibration_models:
  arbitrary_slot: {provider: selected-provider, model: model-selected, temperature: 0.0, max_tokens: 32000}
pricing:
  models:
    selected-provider/model-selected: {input_usd_per_million_tokens: 1, output_usd_per_million_tokens: 1}
""",
        encoding="utf-8",
    )
    limits = tmp_path / "limits.json"
    # The exact-algorithm prompt and a full repair context require slightly
    # more than the former synthetic 100k fixture window.
    limits.write_text(json.dumps({"arbitrary_slot": 110000}), encoding="utf-8")
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
    assert set(preflight.context_limits) == {"arbitrary_slot"}
    broken = config.read_text(encoding="utf-8").replace("selected-provider/model-selected:", "missing/model:")
    config.write_text(broken, encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="missing pricing"):
        preflight_calibration_config(config, limits, require_environment=False)


def test_neutrality_rejects_protocol_and_model_specific_source():
    with pytest.raises(PromptDevelopmentError, match="neutrality"):
        scan_prompt_neutrality("Use MQTT and model-qwen in the prompt")
    scan_prompt_neutrality("Use only the supplied delimited artifacts and generic contracts")


def test_init_binds_lineage_and_runs_one_configured_model_slot(tmp_path):
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
    assert result["assessment"]["trial_count"] == 3
    assert result["assessment"]["attempt"] == 1
    assert result["assessment"]["screening_pass"] is False
    assert set(instances) == {"arbitrary_slot"}
    assert (coordinator.root / "prompt-development/versions/v0/initial.md").read_bytes() == Path("nepa/agents/prompts/architecture_planner_initial.md").read_bytes()
    summary = write_development_report(
        coordinator.root,
        config_path=config,
        context_limits_path=limits,
        output_dir=tmp_path / "results",
    )
    assert set(summary["versions"]["v0"]["slots"]) == {"arbitrary_slot"}
    report_path = tmp_path / "results/development-report.md"
    validate_development_report(summary, report_path.read_text(encoding="utf-8"))
    for model_id in ("arbitrary_slot",):
        value = json.loads((coordinator.root / "v0" / model_id / "batch.json").read_text(encoding="utf-8"))
        assert value["trial_count"] == 3
        assert value["semantic_depth"] == 2
        assert value["repair_mode"] == "patch"
        assert value["prompt_sha256"] == protocol_prompt_hash(coordinator.root)


def protocol_prompt_hash(root: Path) -> str:
    import hashlib
    return hashlib.sha256((root / "prompt-development/versions/v0/initial.md").read_bytes()).hexdigest()


def test_snapshot_mutation_and_selection_block_provider_work(tmp_path):
    config, limits = _config_files(tmp_path)
    instances = {}
    coordinator = PromptDevelopmentCoordinator.init(config_path=config, context_limits_path=limits, runs_root=tmp_path / "runs", provider_factory=_factory(instances), require_environment=False)
    snapshot = coordinator.root / "prompt-development/versions/v0/initial.md"
    original = snapshot.read_bytes()
    snapshot.write_bytes(original + b"\n")
    with pytest.raises(PromptDevelopmentEvidenceError):
        coordinator.run_version("v0")


def test_two_of_three_selects_immediately_and_waits_for_owner_before_handoff(tmp_path):
    from test_architecture_validation import _valid_draft
    valid, _planning, _manifest, _constraints = _valid_draft()

    class TwoOfThreeProvider:
        native_structured_output = False

        def __init__(self):
            self.calls = 0

        def complete(self, request, *, model, native_schema):
            self.calls += 1
            if self.calls <= 3:
                from nepa.llm.client import TransportError
                raise TransportError("one unavailable sample", provider="selected-provider")
            return LLMResponse(
                text=json.dumps(valid), tokens_in=1, tokens_out=1, cost_usd=0, model=model,
                parameter_support={"temperature": ParameterSupportState.UNKNOWN},
            )

    instances = {}
    config, limits = _config_files(tmp_path)
    coordinator = PromptDevelopmentCoordinator.init(
        config_path=config,
        context_limits_path=limits,
        runs_root=tmp_path / "runs",
        provider_factory=_factory(instances),
        require_environment=False,
    )
    provider = TwoOfThreeProvider()
    coordinator.provider_factory = lambda model_id, target, *_args: {target.provider: provider}
    result = coordinator.run_version("v0")
    assert result["assessment"]["models"]["arbitrary_slot"]["p2_passes"] == 2
    assert result["assessment"]["screening_pass"] is True
    assert (coordinator.root / "prompt-development/selection.json").is_file()
    assert not (coordinator.root / "prompt-development/handoff.json").exists()
    assert coordinator.next_action("v0") == {"action": "terminal-selection"}
    invalid_path = coordinator.root / "prompt-development/invalid-approval.json"
    invalid_path.write_text(json.dumps({"approved": False, "reviewer": "test-owner"}), encoding="utf-8")
    invalid_ref = {"path": "prompt-development/invalid-approval.json", "sha256": __import__("hashlib").sha256(invalid_path.read_bytes()).hexdigest()}
    with pytest.raises(PromptDevelopmentEvidenceError, match="invalid owner approval"):
        coordinator.publish_handoff(invalid_ref)
    approval_path = coordinator.root / "prompt-development/owner-approval.json"
    approval_path.write_text(json.dumps({"approved": True, "reviewer": "test-owner"}), encoding="utf-8")
    approval_ref = {"path": "prompt-development/owner-approval.json", "sha256": __import__("hashlib").sha256(approval_path.read_bytes()).hexdigest()}
    handoff_ref = coordinator.publish_handoff(approval_ref)
    handoff = json.loads((coordinator.root / handoff_ref["path"]).read_text(encoding="utf-8"))
    assert handoff["consumer"] == "m1-4c"
    assert handoff["satisfies"]["production_quality_proven"] is False


def test_fixed_key_mapping_is_checked_without_reading_values(tmp_path, monkeypatch):
    config, limits = _config_files(tmp_path)
    monkeypatch.delenv("NEPA_SELECTED_API_KEY", raising=False)
    with pytest.raises(PromptDevelopmentConfigError, match="NEPA_SELECTED_API_KEY"):
        preflight_calibration_config(config, limits, require_environment=True)


def test_revision_can_change_both_prompt_stages(tmp_path):
    config, limits = _config_files(tmp_path)
    initial_source = tmp_path / "architecture_planner_initial.md"
    repair_source = tmp_path / "architecture_planner_repair.md"
    initial_source.write_bytes(Path("nepa/agents/prompts/architecture_planner_initial.md").read_bytes())
    repair_source.write_bytes(Path("nepa/agents/prompts/architecture_planner_repair.md").read_bytes())
    instances = {}
    coordinator = PromptDevelopmentCoordinator.init(
        config_path=config, context_limits_path=limits, runs_root=tmp_path / "runs",
        provider_factory=_factory(instances), initial_prompt_source_path=initial_source, repair_prompt_source_path=repair_source, require_environment=False,
    )
    result = coordinator.run_version("v0")
    assert result["assessment"]["screening_pass"] is False
    evidence = {"path": "prompt-development/versions/v0/assessment-n003.json", "sha256": __import__("hashlib").sha256((coordinator.root / "prompt-development/versions/v0/assessment-n003.json").read_bytes()).hexdigest()}
    initial_source.write_bytes(initial_source.read_bytes() + b"\nUse a final generic consistency checklist.\n")
    repair_source.write_bytes(repair_source.read_bytes() + b"\nRecheck all current issues together.\n")
    revision = coordinator.record_revision("v1", hypothesis="The construction and repair instructions need one coordinated clarification.", evidence_refs=[evidence], expected_gates=["arch_07"], stopping_conclusion="Stop revising if the next complete N=3 attempt still fails screening.")
    assert revision["version"] == "v1"
    assert (coordinator.root / "prompt-development/versions/v1/revision.json").is_file()
    revision_record = json.loads((coordinator.root / "prompt-development/versions/v1/revision.json").read_text())
    assert revision_record["changed_stages"] == ["initial", "repair"]
    v1_result = coordinator.run_version("v1")
    assert v1_result["assessment"]["status"] == "complete"
    v1_evidence_path = coordinator.root / "prompt-development/versions/v1/assessment-n003.json"
    v1_evidence = {"path": "prompt-development/versions/v1/assessment-n003.json", "sha256": __import__("hashlib").sha256(v1_evidence_path.read_bytes()).hexdigest()}
    with pytest.raises(PromptDevelopmentError, match="change at least one"):
        coordinator.record_revision("v2", hypothesis="The self-check order is underspecified.", evidence_refs=[v1_evidence], prompt_bytes=initial_source.read_bytes(), stopping_conclusion="Stop revising after this hypothesis is falsified.")


def test_v2_failure_publishes_only_an_earlier_version_diagnostic(tmp_path):
    config, limits = _config_files(tmp_path)
    initial_source = tmp_path / "architecture_planner_initial.md"
    repair_source = tmp_path / "architecture_planner_repair.md"
    initial_source.write_bytes(Path("nepa/agents/prompts/architecture_planner_initial.md").read_bytes())
    repair_source.write_bytes(Path("nepa/agents/prompts/architecture_planner_repair.md").read_bytes())
    instances = {}
    coordinator = PromptDevelopmentCoordinator.init(
        config_path=config, context_limits_path=limits, runs_root=tmp_path / "runs",
        provider_factory=_factory(instances), initial_prompt_source_path=initial_source,
        repair_prompt_source_path=repair_source, require_environment=False,
    )

    def assessment_ref(version):
        relative = f"prompt-development/versions/{version}/assessment-n003.json"
        data = (coordinator.root / relative).read_bytes()
        return {"path": relative, "sha256": __import__("hashlib").sha256(data).hexdigest()}

    coordinator.run_version("v0")
    initial_source.write_bytes(initial_source.read_bytes() + b"\nCheck the generic construction order once.\n")
    coordinator.record_revision(
        "v1", hypothesis="Clarify the generic construction check.", evidence_refs=[assessment_ref("v0")],
        stopping_conclusion="Continue once only if the complete batch remains below the baseline.",
    )
    coordinator.run_version("v1")
    repair_source.write_bytes(repair_source.read_bytes() + b"\nCheck every currently allowed issue together.\n")
    coordinator.record_revision(
        "v2", hypothesis="Clarify the final allowed-issue check.", evidence_refs=[assessment_ref("v1")],
        stopping_conclusion="Stop prompt development after this complete batch.",
    )
    result = coordinator.run_version("v2")
    diagnostic = json.loads((coordinator.root / "prompt-development/diagnostic-selection.json").read_text(encoding="utf-8"))
    assert result["assessment"]["screening_pass"] is False
    assert diagnostic["status"] == "diagnostic"
    assert diagnostic["selected_version"] == "v0"
    assert not (coordinator.root / "prompt-development/selection.json").exists()
    assert not (coordinator.root / "prompt-development/handoff.json").exists()
    assert coordinator._declared_initial_trial_count() == 9
    assert coordinator.next_action("v2") == {"action": "terminal-diagnostic", "handoff": False}
