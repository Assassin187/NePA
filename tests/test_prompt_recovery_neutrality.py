from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentError, scan_prompt_neutrality


def test_exact_algorithm_seed_is_protocol_model_provider_neutral():
    seed = Path("experiments/m1-4a2-architecture-planner-prompt-optimization/results/phase1/artifacts/prompt-exact-algorithm.md").read_bytes()
    scan_prompt_neutrality(seed, forbidden_tokens={"model": "qwen3.7-max-2026-06-08", "provider": "deepseek"})


@pytest.mark.parametrize("token", ["MQTT", "qwen3.7-max-2026-06-08", "deepseek"])
def test_specialized_recovery_prompt_is_rejected(token):
    with pytest.raises(PromptDevelopmentError, match="neutrality violation"):
        scan_prompt_neutrality(f"shared instructions\n{token}", forbidden_tokens={"forbidden": token})
