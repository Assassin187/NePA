import pytest

from nepa.calibration.s4_prompt_development import (
    RECOVERY_AUTHORIZATION_ENV,
    RECOVERY_CONFIG_ENV,
    RECOVERY_CONTEXT_LIMITS_ENV,
    PromptDevelopmentConfigError,
    preflight_calibration_config,
)


def test_active_baseline_uses_one_configured_slot():
    assert (RECOVERY_AUTHORIZATION_ENV, RECOVERY_CONFIG_ENV, RECOVERY_CONTEXT_LIMITS_ENV) == (
        "NEPA_M1_4A2R_AUTHORIZATION", "NEPA_M1_4A2R_CONFIG", "NEPA_M1_4A2R_CONTEXT_LIMITS"
    )
    preflight = preflight_calibration_config("configs/m1-4a2-live.yaml", "configs/m1-4a2-context-limits.json", require_environment=False)
    assert set(preflight.model_projection) == {"architecture_primary"}
    assert all(item["max_tokens"] == 65536 for item in preflight.model_projection.values())
    assert preflight.model_projection["architecture_primary"]["provider"] == "anthropic"
    assert preflight.model_projection["architecture_primary"]["model"] == "claude-opus-5"


def test_active_config_requires_only_its_configured_process_environment(monkeypatch):
    monkeypatch.delenv("NEPA_CLAUDE_API_KEY", raising=False)
    with pytest.raises(PromptDevelopmentConfigError, match="missing required environment variable"):
        preflight_calibration_config("configs/m1-4a2-live.yaml", "configs/m1-4a2-context-limits.json", require_environment=True)
