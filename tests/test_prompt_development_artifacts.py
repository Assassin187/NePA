import json

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentEvidenceError, _publish_bytes, _publish_json


def test_development_artifacts_are_immutable_and_replayable(tmp_path):
    value = {"schema_version": "2.0", "lineage_id": "a" * 64, "version": "v0", "prompt_ref": {"path": "prompt.md", "sha256": "b" * 64}, "prompt_sha256": "b" * 64, "source_template_sha256": "b" * 64, "byte_encoding": "utf-8-raw-template"}
    first = _publish_json(tmp_path, "prompt-development/versions/v0/snapshot.json", value, "snapshot")
    second = _publish_json(tmp_path, "prompt-development/versions/v0/snapshot.json", value, "snapshot")
    assert first == second
    with pytest.raises(PromptDevelopmentEvidenceError):
        _publish_bytes(tmp_path, "prompt-development/versions/v0/snapshot.json", b"different")


def test_orphan_staging_is_not_a_committed_record(tmp_path):
    staging = tmp_path / "prompt-development/versions/v0/.attempt.staging"
    staging.mkdir(parents=True)
    (staging / "outcome.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    assert not (tmp_path / "prompt-development/versions/v0/outcome.json").exists()
