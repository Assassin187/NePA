import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentError, scan_prompt_neutrality


def test_neutrality_rejects_protocol_names_ports_and_configured_facts():
    with pytest.raises(PromptDevelopmentError):
        scan_prompt_neutrality("generic guidance for MQTT port 1883")
    with pytest.raises(PromptDevelopmentError):
        scan_prompt_neutrality("generic guidance for model-qwen", forbidden_tokens={"model": "model-qwen"})
