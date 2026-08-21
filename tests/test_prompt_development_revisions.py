import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentError, _version_dir


def test_revision_versions_are_bounded_and_single_hypothesis_inputs_are_not_relaxed():
    with pytest.raises(PromptDevelopmentError):
        _version_dir("v3")
    with pytest.raises(PromptDevelopmentError):
        _version_dir("../v1")
