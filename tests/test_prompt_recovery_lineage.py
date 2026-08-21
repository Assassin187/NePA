import json
import shutil
from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import (
    RECOVERY_PREDECESSOR_LINEAGE,
    PromptDevelopmentEvidenceError,
    attest_predecessor_tie,
)


PREDECESSOR = Path("runs/_calibration/s4-architecture") / RECOVERY_PREDECESSOR_LINEAGE


def test_exact_predecessor_recomputes_to_the_authorized_tie_without_current_component_check():
    attestation = attest_predecessor_tie(PREDECESSOR)
    assert attestation["outcome"] == "PROMPT_SELECTION_TIE"
    assert attestation["fallback_tuples"] == {version: [0.0, 0.0, 1.0, 0.0] for version in ("v0", "v1", "v2")}
    assert attestation["selection_absent"] is True


def test_selected_or_mutated_predecessor_is_rejected(tmp_path):
    copied = tmp_path / RECOVERY_PREDECESSOR_LINEAGE
    shutil.copytree(PREDECESSOR, copied)
    selection = copied / "prompt-development/selection.json"
    selection.write_text("{}", encoding="utf-8")
    with pytest.raises(PromptDevelopmentEvidenceError, match="selected predecessor"):
        attest_predecessor_tie(copied, workspace_root=tmp_path)
    selection.unlink()
    report = next(copied.glob("v0/*/calibration_report.json"))
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(PromptDevelopmentEvidenceError, match="hash mismatch"):
        attest_predecessor_tie(copied, workspace_root=tmp_path)
