import json

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentEvidenceError, _publish_recovery_json


def test_recovery_publication_is_canonical_idempotent_and_conflict_rejecting(tmp_path):
    value = json.loads(open("nepa/schemas/examples/calibration-recovery-prompt-snapshot.example.json", encoding="utf-8").read())
    first = _publish_recovery_json(tmp_path, "r0/snapshot.json", value, "snapshot")
    assert _publish_recovery_json(tmp_path, "r0/snapshot.json", value, "snapshot") == first
    changed = dict(value)
    changed["prompt_sha256"] = "b" * 64
    with pytest.raises(PromptDevelopmentEvidenceError, match="differs"):
        _publish_recovery_json(tmp_path, "r0/snapshot.json", changed, "snapshot")


def test_recovery_record_cannot_reference_parent_or_absolute_path(tmp_path):
    value = json.loads(open("nepa/schemas/examples/calibration-recovery-prompt-snapshot.example.json", encoding="utf-8").read())
    value["prompt_ref"]["path"] = "../old/trial.json"
    with pytest.raises(PromptDevelopmentEvidenceError, match="invalid"):
        _publish_recovery_json(tmp_path, "r0/snapshot.json", value, "snapshot")
