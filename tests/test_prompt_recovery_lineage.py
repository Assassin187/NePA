from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentEvidenceError, attest_predecessor_tie


HISTORICAL_ROOT = Path("runs/_calibration/s4-architecture/daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772")


def test_historical_dual_model_predecessor_is_rejected_as_obsolete():
    with pytest.raises(PromptDevelopmentEvidenceError, match="current-contract"):
        attest_predecessor_tie(HISTORICAL_ROOT)


def test_non_lineage_predecessor_is_rejected(tmp_path):
    with pytest.raises(PromptDevelopmentEvidenceError, match="lineage root"):
        attest_predecessor_tie(tmp_path / "not-a-lineage", workspace_root=tmp_path)
