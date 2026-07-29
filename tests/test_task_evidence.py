"""Task Evidence v1 producer/validator 测试（设计 5.4、6.6）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes
from nepa.task_evidence import (
    TaskEvidenceValidationError,
    publish_task_evidence,
    validate_task_evidence_ref,
)

PLAN_SHA = "ab" * 32
TREE_SHA = "c" * 40


def _write_ref(run_dir: Path, relative: str, value: dict[str, Any]) -> dict[str, str]:
    path = run_dir / relative
    atomic_write_canonical_json(path, value)
    return {
        "path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _build_ref(run_dir: Path) -> dict[str, str]:
    return _write_ref(
        run_dir,
        "test_results/task_evidence/T-001/build_attempt_001.json",
        {"variant": "release", "ok": True},
    )


def test_publish_build_only_evidence_is_canonical_and_self_validating(
    tmp_path: Path,
) -> None:
    build_ref = _build_ref(tmp_path)

    result = publish_task_evidence(
        tmp_path,
        task_id="T-001",
        attempt=1,
        plan_sha256=PLAN_SHA,
        workspace_tree=TREE_SHA,
        build_result_refs=[build_ref],
        test_summary_refs=[],
    )

    path = tmp_path / result.ref["path"]
    assert path.read_bytes() == canonical_json_bytes(result.value)
    assert result.value["build_result_refs"] == [build_ref]
    assert result.value["test_summary_refs"] == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result.ref["sha256"]


def test_publish_with_test_summary_and_exact_frozen_bindings(tmp_path: Path) -> None:
    build_ref = _build_ref(tmp_path)
    summary_ref = _write_ref(
        tmp_path,
        "test_results/round_001/summary.json",
        {"schema_version": "2.0", "round_id": 1},
    )
    result = publish_task_evidence(
        tmp_path,
        task_id="T-001",
        attempt=1,
        plan_sha256=PLAN_SHA,
        workspace_tree=TREE_SHA,
        build_result_refs=[build_ref],
        test_summary_refs=[summary_ref],
    )

    validated = validate_task_evidence_ref(
        tmp_path,
        result.ref,
        task_id="T-001",
        attempt=1,
        plan_sha256=PLAN_SHA,
        workspace_tree=TREE_SHA,
    )

    assert validated == result


def test_publish_is_idempotent_but_refuses_different_immutable_content(
    tmp_path: Path,
) -> None:
    build_ref = _build_ref(tmp_path)
    kwargs = {
        "task_id": "T-001",
        "attempt": 1,
        "plan_sha256": PLAN_SHA,
        "workspace_tree": TREE_SHA,
        "build_result_refs": [build_ref],
        "test_summary_refs": [],
    }
    first = publish_task_evidence(tmp_path, **kwargs)
    assert publish_task_evidence(tmp_path, **kwargs) == first

    other_ref = _write_ref(
        tmp_path,
        "test_results/task_evidence/T-001/build_other.json",
        {"variant": "san", "ok": True},
    )
    with pytest.raises(TaskEvidenceValidationError, match="内容不同"):
        publish_task_evidence(
            tmp_path,
            **{**kwargs, "build_result_refs": [other_ref]},
        )


def test_schema_requires_at_least_one_build_receipt(tmp_path: Path) -> None:
    with pytest.raises(TaskEvidenceValidationError, match="non-empty"):
        publish_task_evidence(
            tmp_path,
            task_id="T-001",
            attempt=1,
            plan_sha256=PLAN_SHA,
            workspace_tree=TREE_SHA,
            build_result_refs=[],
            test_summary_refs=[],
        )


def test_source_receipt_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    build_ref = _build_ref(tmp_path)
    build_ref["sha256"] = "00" * 32

    with pytest.raises(TaskEvidenceValidationError, match="SHA-256"):
        publish_task_evidence(
            tmp_path,
            task_id="T-001",
            attempt=1,
            plan_sha256=PLAN_SHA,
            workspace_tree=TREE_SHA,
            build_result_refs=[build_ref],
            test_summary_refs=[],
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_id", "T-002", "path"),
        ("attempt", 2, "path"),
        ("plan_sha256", "00" * 32, "plan_sha256"),
        ("workspace_tree", "d" * 40, "workspace_tree"),
    ],
)
def test_validation_rejects_wrong_expected_frozen_fact(
    tmp_path: Path,
    field: str,
    value: Any,
    message: str,
) -> None:
    build_ref = _build_ref(tmp_path)
    result = publish_task_evidence(
        tmp_path,
        task_id="T-001",
        attempt=1,
        plan_sha256=PLAN_SHA,
        workspace_tree=TREE_SHA,
        build_result_refs=[build_ref],
        test_summary_refs=[],
    )
    expected = {
        "task_id": "T-001",
        "attempt": 1,
        "plan_sha256": PLAN_SHA,
        "workspace_tree": TREE_SHA,
    }
    expected[field] = value

    with pytest.raises(TaskEvidenceValidationError, match=message):
        validate_task_evidence_ref(tmp_path, result.ref, **expected)


def test_validation_rejects_tampered_evidence_file(tmp_path: Path) -> None:
    build_ref = _build_ref(tmp_path)
    result = publish_task_evidence(
        tmp_path,
        task_id="T-001",
        attempt=1,
        plan_sha256=PLAN_SHA,
        workspace_tree=TREE_SHA,
        build_result_refs=[build_ref],
        test_summary_refs=[],
    )
    (tmp_path / result.ref["path"]).write_text("{}", encoding="utf-8")

    with pytest.raises(TaskEvidenceValidationError, match="自身 SHA-256"):
        validate_task_evidence_ref(
            tmp_path,
            result.ref,
            task_id="T-001",
            attempt=1,
            plan_sha256=PLAN_SHA,
            workspace_tree=TREE_SHA,
        )


def test_validation_rejects_noncanonical_evidence_even_with_matching_hash(
    tmp_path: Path,
) -> None:
    build_ref = _build_ref(tmp_path)
    result = publish_task_evidence(
        tmp_path,
        task_id="T-001",
        attempt=1,
        plan_sha256=PLAN_SHA,
        workspace_tree=TREE_SHA,
        build_result_refs=[build_ref],
        test_summary_refs=[],
    )
    path = tmp_path / result.ref["path"]
    path.write_text(
        json.dumps(result.value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ref = {
        "path": result.ref["path"],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }

    with pytest.raises(TaskEvidenceValidationError, match="canonical"):
        validate_task_evidence_ref(
            tmp_path,
            ref,
            task_id="T-001",
            attempt=1,
            plan_sha256=PLAN_SHA,
            workspace_tree=TREE_SHA,
        )
