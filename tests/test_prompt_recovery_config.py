import pytest

from nepa.calibration.s4_prompt_development import (
    RECOVERY_AUTHORIZATION_ENV,
    RECOVERY_CONFIG_ENV,
    RECOVERY_CONTEXT_LIMITS_ENV,
    PromptDevelopmentConfigError,
    preflight_calibration_config,
)


def test_recovery_uses_fixed_explicit_input_names_only():
    assert (RECOVERY_AUTHORIZATION_ENV, RECOVERY_CONFIG_ENV, RECOVERY_CONTEXT_LIMITS_ENV) == (
        "NEPA_M1_4A2R_AUTHORIZATION", "NEPA_M1_4A2R_CONFIG", "NEPA_M1_4A2R_CONTEXT_LIMITS"
    )
    preflight = preflight_calibration_config("configs/m1-4a2-live.yaml", "configs/m1-4a2-context-limits.json", require_environment=False)
    assert set(preflight.model_projection) == {"qwen", "claude", "deepseek"}
    assert all(item["max_tokens"] == 65536 for item in preflight.model_projection.values())


def test_recovery_config_requires_process_environment_without_reading_dotenv(monkeypatch):
    monkeypatch.delenv("NEPA_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("NEPA_DS_API_KEY", raising=False)
    with pytest.raises(PromptDevelopmentConfigError, match="missing required environment variable"):
        preflight_calibration_config("configs/m1-4a2-live.yaml", "configs/m1-4a2-context-limits.json", require_environment=True)
