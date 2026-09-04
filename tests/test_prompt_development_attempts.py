import json

import pytest

from nepa.calibration.s4_prompt_development import PromptDevelopmentCoordinator, PromptDevelopmentError, PromptDevelopmentEvidenceError


def _declaration(lineage_id: str, version: str, attempt: int):
    return {
        "schema_version": "2.0", "lineage_id": lineage_id, "version": version,
        "attempt": attempt, "status": "declared",
        "prompt_ref": {"path": "prompt-development/versions/v0/prompt.md", "sha256": "a" * 64},
        "prompt_sha256": "a" * 64, "trial_count": 3, "semantic_depth": 2,
        "repair_mode": "patch",
        "initial_trial_ids": {name: ["trial_001", "trial_002", "trial_003"] for name in ("qwen", "claude", "deepseek")},
        "model_ids": ["qwen", "claude", "deepseek"],
    }


def _outcome(lineage_id: str, version: str, attempt: int, status: str):
    return {
        "schema_version": "2.0", "lineage_id": lineage_id, "version": version,
        "attempt": attempt, "status": status,
        "reports": {"qwen": None, "claude": None, "deepseek": None},
    }


def test_failed_version_attempt_is_preserved_and_not_rerun(tmp_path):
    lineage_id = "b" * 64
    root = tmp_path / lineage_id
    attempt_dir = root / "prompt-development/versions/v0/attempts/attempt_001"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "declaration.json").write_text(json.dumps(_declaration(lineage_id, "v0", 1)), encoding="utf-8")
    (attempt_dir / "outcome.json").write_text(json.dumps(_outcome(lineage_id, "v0", 1, "infrastructure-invalid")), encoding="utf-8")
    coordinator = PromptDevelopmentCoordinator(root)
    with pytest.raises(PromptDevelopmentError, match="cannot be rerun"):
        coordinator._next_attempt("v0")


def test_next_attempt_rejects_gapped_attempt_history(tmp_path):
    lineage_id = "c" * 64
    root = tmp_path / lineage_id
    attempt_dir = root / "prompt-development/versions/v0/attempts/attempt_002"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "declaration.json").write_text(json.dumps(_declaration(lineage_id, "v0", 2)), encoding="utf-8")
    (attempt_dir / "outcome.json").write_text(json.dumps(_outcome(lineage_id, "v0", 2, "infrastructure-invalid")), encoding="utf-8")
    with pytest.raises(PromptDevelopmentEvidenceError, match="monotonic"):
        PromptDevelopmentCoordinator(root)._next_attempt("v0")
