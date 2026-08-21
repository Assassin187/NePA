import hashlib
import json
from pathlib import Path

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentEvidenceError, verify_recovery_authorization


def _authorization(design: Path, workspace: Path):
    return {
        "schema_version": "1.0",
        "change": "m1-4a2r-architecture-planner-calibration-recovery",
        "decision_id": "owner-approved-recovery",
        "responsible_owner": "responsible-owner",
        "approved": True,
        "design_version": "3.1.0",
        "approved_design": {
            "workspace_path": design.relative_to(workspace).as_posix(),
            "sha256": hashlib.sha256(design.read_bytes()).hexdigest(),
        },
        "protocol": {
            "entry_condition": "PROMPT_SELECTION_TIE",
            "versions": ["r0", "r1", "r2"],
            "prompt_edit_limit": 2,
            "p0_role": "diagnostic",
            "completion_boundary": "m1-4a3-admission-only",
        },
    }


def test_authorization_binds_exact_design_bytes_before_side_effects(tmp_path):
    design = tmp_path / "project_docs/system_design.md"
    design.parent.mkdir()
    design.write_text("设计版本：3.1.1 M1-4a2r PROMPT_SELECTION_TIE R0 R1 R2 M1-4a3", encoding="utf-8")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(_authorization(design, tmp_path)), encoding="utf-8")
    assert verify_recovery_authorization(authorization, design, workspace_root=tmp_path)["approved"] is True
    design.write_text(design.read_text(encoding="utf-8") + " drift", encoding="utf-8")
    with pytest.raises(PromptDevelopmentEvidenceError, match="path or SHA-256"):
        verify_recovery_authorization(authorization, design, workspace_root=tmp_path)


def test_authorization_rejects_open_or_secret_fields(tmp_path):
    design = tmp_path / "project_docs/system_design.md"
    design.parent.mkdir()
    design.write_text("设计版本：3.1.0 M1-4a2r PROMPT_SELECTION_TIE R0 R1 R2 M1-4a3", encoding="utf-8")
    value = _authorization(design, tmp_path)
    value["api_key"] = "forbidden"
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PromptDevelopmentEvidenceError, match="Additional properties"):
        verify_recovery_authorization(authorization, design, workspace_root=tmp_path)
