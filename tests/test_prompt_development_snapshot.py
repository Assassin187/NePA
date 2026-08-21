from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentCoordinator, PromptDevelopmentEvidenceError, _root_ref
from nepa.calibration.s4_architecture import CalibrationDeclarationError


def test_snapshot_source_guard_is_byte_exact(tmp_path):
    source = tmp_path / "architecture_planner.md"
    source.write_bytes(b"generic prompt")
    coordinator = PromptDevelopmentCoordinator(tmp_path, prompt_source_path=source)
    guard = coordinator._source_guard(b"generic prompt")
    guard()
    source.write_bytes(b"drifted prompt")
    with pytest.raises(CalibrationDeclarationError, match="drifted"):
        guard()


def test_snapshot_path_is_confined_by_the_development_reference_rules(tmp_path):
    with pytest.raises(PromptDevelopmentEvidenceError):
        _root_ref(tmp_path, "../escaped")
