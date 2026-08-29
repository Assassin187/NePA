import json
from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentError, PromptDevelopmentEvidenceError, PromptRecoveryCoordinator


ROOT = Path(__file__).parents[1].resolve()
HISTORICAL_ROOT = ROOT / "runs/_calibration/s4-architecture/daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772"


def _coordinator(tmp_path):
    return PromptRecoveryCoordinator(tmp_path / ("a" * 64) / "prompt-recovery", workspace_root=tmp_path, require_environment=False)


def test_recovery_rejects_historical_predecessor_before_provider_io(tmp_path):
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    with pytest.raises(PromptDevelopmentEvidenceError):
        PromptRecoveryCoordinator.init(
            authorization_path=authorization,
            design_path=ROOT / "project_docs/system_design.md",
            config_path=ROOT / "configs/m1-4a2-live.yaml",
            context_limits_path=ROOT / "configs/m1-4a2-context-limits.json",
            spec_path=ROOT / "gold_file/specIR.json",
            target_path=ROOT / "gold_file/target.json",
            test_bundle_path=ROOT / "gold_file/test_bundle.json",
            predecessor_root=HISTORICAL_ROOT,
            experiment_root=ROOT / "experiments/m1-4a2-architecture-planner-prompt-optimization",
            seed_path=ROOT / "nepa/agents/prompts/architecture_planner.md",
            seed_sha256="0" * 64,
            runs_root=tmp_path / "runs",
            workspace_root=ROOT,
            prompt_source_path=tmp_path / "architecture_planner.md",
            require_environment=False,
        )
    assert not (tmp_path / "architecture_planner.md").exists()


def test_recovery_rejects_r3_before_any_authority_or_provider_io(tmp_path):
    coordinator = _coordinator(tmp_path)
    with pytest.raises(PromptDevelopmentError, match="R3"):
        coordinator.run_version("r3")
