import json

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentConfigError, preflight_calibration_config

from test_prompt_development import _config_files


def test_fixed_key_names_and_sentinel_values_never_enter_public_projection(tmp_path, monkeypatch):
    config, limits = _config_files(tmp_path)
    sentinels = {
        "NEPA_SELECTED_API_KEY": "sentinel-selected-value",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    preflight = preflight_calibration_config(config, limits, require_environment=True)
    encoded = json.dumps({"snapshot": preflight.config_snapshot, "sha256": preflight.config_sha256})
    assert all(value not in encoded for value in sentinels.values())
    assert all(value not in preflight.config_sha256 for value in sentinels.values())
    assert set(preflight.config_snapshot["providers"]["selected-provider"]) == {"kind", "base_url", "api_key_env"}


def test_configured_key_name_is_not_hard_coded(tmp_path, monkeypatch):
    config, limits = _config_files(tmp_path)
    value = config.read_text(encoding="utf-8").replace("NEPA_SELECTED_API_KEY", "ANOTHER_VALID_KEY")
    config.write_text(value, encoding="utf-8")
    monkeypatch.setenv("ANOTHER_VALID_KEY", "secret")
    assert preflight_calibration_config(config, limits, require_environment=True).config.providers["selected-provider"].api_key_env == "ANOTHER_VALID_KEY"


def test_context_limits_reject_missing_extra_and_non_positive_values(tmp_path):
    config, limits = _config_files(tmp_path)
    limits.write_text(json.dumps({"wrong_slot": 1}), encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="exactly"):
        preflight_calibration_config(config, limits, require_environment=False)
    limits.write_text(json.dumps({"arbitrary_slot": 0}), encoding="utf-8")
    with pytest.raises(PromptDevelopmentConfigError, match="positive"):
        preflight_calibration_config(config, limits, require_environment=False)


def test_max_tokens_may_come_from_configuration(tmp_path):
    config, limits = _config_files(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("max_tokens: 32000", "max_tokens: 16000", 1), encoding="utf-8")
    assert preflight_calibration_config(config, limits, require_environment=False).config.calibration_models["arbitrary_slot"].max_tokens == 16000
