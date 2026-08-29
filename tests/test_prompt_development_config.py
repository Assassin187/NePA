import json

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentConfigError, preflight_calibration_config

from test_prompt_development import _config_files


def test_fixed_key_names_and_sentinel_values_never_enter_public_projection(tmp_path, monkeypatch):
    config, limits = _config_files(tmp_path)
    sentinels = {
        "NEPA_QWEN_API_KEY": "sentinel-qwen-value",
        "NEPA_CLAUDE_API_KEY": "sentinel-claude-value",
        "NEPA_DS_API_KEY": "sentinel-deepseek-value",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    preflight = preflight_calibration_config(config, limits, require_environment=True)
    encoded = json.dumps({"snapshot": preflight.config_snapshot, "sha256": preflight.config_sha256})
    assert all(value not in encoded for value in sentinels.values())
    assert all(value not in preflight.config_sha256 for value in sentinels.values())
    assert set(preflight.config_snapshot["providers"]["qwen-provider"]) == {"kind", "base_url", "api_key_env"}


def test_alternate_key_name_is_rejected_before_lineage_work(tmp_path):
    config, limits = _config_files(tmp_path)
    value = config.read_text(encoding="utf-8").replace("NEPA_DS_API_KEY", "UNAUTHORIZED_KEY")
    config.write_text(value, encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="NEPA_DS_API_KEY"):
        preflight_calibration_config(config, limits, require_environment=False)


def test_context_limits_reject_missing_extra_and_non_positive_values(tmp_path):
    config, limits = _config_files(tmp_path)
    limits.write_text(json.dumps({"qwen": 1}), encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="exactly"):
        preflight_calibration_config(config, limits, require_environment=False)
    limits.write_text(json.dumps({"qwen": 1, "claude": 1, "deepseek": 0}), encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="positive"):
        preflight_calibration_config(config, limits, require_environment=False)


def test_max_tokens_must_be_the_fixed_calibration_budget(tmp_path):
    config, limits = _config_files(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("max_tokens: 65536", "max_tokens: 16000", 1), encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="invalid request parameters"):
        preflight_calibration_config(config, limits, require_environment=False)
