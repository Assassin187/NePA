import hashlib
import json
from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentError, PromptRecoveryCoordinator
from nepa.llm.client import LLMResponse, ParameterSupportState


ROOT = Path(__file__).parents[1].resolve()
PREDECESSOR = ROOT / "runs/_calibration/s4-architecture/daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772"
EXPERIMENT = ROOT / "experiments/m1-4a2-architecture-planner-prompt-optimization"
SEED = EXPERIMENT / "results/phase1/artifacts/prompt-exact-algorithm.md"
PASSING_CANDIDATE = EXPERIMENT / "results/phase1/runs/e1-1-b-exact-prompt/deepseek/trial_001_p0.candidate.json"


class _PassingProvider:
    native_structured_output = False

    def complete(self, request, *, model, native_schema):
        return LLMResponse(
            text=PASSING_CANDIDATE.read_text(encoding="utf-8"),
            tokens_in=10,
            tokens_out=20,
            cost_usd=0,
            model=model,
            provider_metadata={"finish_reason": "stop"},
            parameter_support={"temperature": ParameterSupportState.REPORTED_APPLIED},
        )


def _authorization(path: Path):
    design = ROOT / "project_docs/system_design.md"
    value = {
        "schema_version": "1.0",
        "change": "m1-4a2r-architecture-planner-calibration-recovery",
        "decision_id": "owner-approved-recovery",
        "responsible_owner": "responsible-owner",
        "approved": True,
        "design_version": "3.1.0",
        "approved_design": {"workspace_path": design.relative_to(Path("/")).as_posix(), "sha256": hashlib.sha256(design.read_bytes()).hexdigest()},
        "protocol": {"entry_condition": "PROMPT_SELECTION_TIE", "versions": ["r0", "r1", "r2"], "prompt_edit_limit": 2, "p0_role": "diagnostic", "completion_boundary": "m1-4a3-admission-only"},
    }
    path.write_text(json.dumps(value), encoding="utf-8")


def _initialize(tmp_path, provider_factory=None):
    authorization = tmp_path / "authorization.json"
    _authorization(authorization)
    prompt = tmp_path / "architecture_planner.md"
    prompt.write_bytes((ROOT / "nepa/agents/prompts/architecture_planner.md").read_bytes())
    coordinator = PromptRecoveryCoordinator.init(
        authorization_path=authorization,
        design_path=ROOT / "project_docs/system_design.md",
        config_path=ROOT / "configs/m1-4a2-live.yaml",
        context_limits_path=ROOT / "configs/m1-4a2-context-limits.json",
        spec_path=ROOT / "gold_file/specIR.json",
        target_path=ROOT / "gold_file/target.json",
        test_bundle_path=ROOT / "gold_file/test_bundle.json",
        predecessor_root=PREDECESSOR,
        experiment_root=EXPERIMENT,
        seed_path=SEED,
        seed_sha256=hashlib.sha256(SEED.read_bytes()).hexdigest(),
        runs_root=tmp_path / "runs",
        workspace_root=Path("/"),
        prompt_source_path=prompt,
        require_environment=False,
        provider_factory=provider_factory,
    )
    return coordinator, prompt


@pytest.fixture
def initialized_recovery(tmp_path):
    return _initialize(tmp_path)


def test_recovery_initialization_creates_fresh_namespace_and_r0_snapshot(initialized_recovery):
    coordinator, prompt = initialized_recovery
    assert coordinator.root.name == "prompt-recovery"
    assert coordinator.lineage_root.name != PREDECESSOR.name
    assert coordinator.next_action() == {"action": "run-version", "version": "r0", "attempt": 1}
    assert prompt.read_bytes() == SEED.read_bytes()
    assert not list(coordinator.root.rglob("trial_*.json"))


def test_recovery_rejects_r3_before_provider_io(initialized_recovery):
    coordinator, _prompt = initialized_recovery
    with pytest.raises(PromptDevelopmentError, match="R3"):
        coordinator.run_version("r3")


def test_first_passing_r0_is_selected_and_handed_only_to_m1_4a3(tmp_path):
    coordinator, prompt = _initialize(tmp_path, provider_factory=lambda model_id, *_args: {model_id: _PassingProvider()})
    result = coordinator.run_version("r0")
    assert result["assessment"]["screening_pass"] is True
    assert result["next_action"] == {"action": "terminal-selection"}
    handoff = json.loads((coordinator.root / "m1-4a3-handoff.json").read_text(encoding="utf-8"))
    assert handoff["consumer"] == "m1-4a3"
    assert handoff["satisfies"] == {"m1_4a3_admission": True, "n20": False, "p2": False, "b1_b4": False, "production_freeze": False, "owner_signature": False, "formal_run": False, "s5_s6": False}
    assert coordinator.recompute(require_complete=True, require_source_match=True)["terminal"]["selected_version"] == "r0"
    assert prompt.read_bytes() == SEED.read_bytes()
    with pytest.raises(PromptDevelopmentError, match="terminal recovery"):
        coordinator.run_version("r1")
