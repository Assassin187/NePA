"""Task Evidence v1 的 canonical producer 与内容校验（设计 5.4、6.6）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "task-evidence.schema.json"


class TaskEvidenceValidationError(ValueError):
    """Task Evidence 结构、引用或冻结绑定不合法。"""


@dataclass(frozen=True, slots=True)
class ValidatedTaskEvidence:
    value: dict[str, Any]
    ref: dict[str, str]


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_schema(value: Any) -> dict[str, Any]:
    errors = sorted(
        _validator().iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, item.absolute_path)) or '<root>'}: {item.message}"
            for item in errors[:8]
        )
        raise TaskEvidenceValidationError(detail)
    if not isinstance(value, dict):
        raise TaskEvidenceValidationError("Task Evidence 顶层必须为 object")
    return value


def _resolve_ref(run_dir: Path, ref: dict[str, Any]) -> Path:
    relative = ref.get("path")
    if not isinstance(relative, str):
        raise TaskEvidenceValidationError("evidence ref path 必须为字符串")
    target = (run_dir / relative).resolve()
    root = run_dir.resolve()
    if not target.is_relative_to(root):
        raise TaskEvidenceValidationError(f"evidence ref 越出 run 目录: {relative}")
    return target


def _validate_source_refs(run_dir: Path, value: dict[str, Any]) -> None:
    for collection in ("build_result_refs", "test_summary_refs"):
        for index, ref in enumerate(value[collection]):
            path = _resolve_ref(run_dir, ref)
            if not path.is_file():
                raise TaskEvidenceValidationError(
                    f"{collection}/{index} 引用文件不存在: {ref['path']}"
                )
            if _sha256_file(path) != ref["sha256"]:
                raise TaskEvidenceValidationError(
                    f"{collection}/{index} 引用文件 SHA-256 不匹配"
                )


def task_evidence_relative_path(task_id: str, attempt: int) -> str:
    """返回设计冻结的 task/attempt 证据路径。"""
    return f"test_results/task_evidence/{task_id}/attempt_{attempt:03d}.json"


def validate_task_evidence_ref(
    run_dir: str | Path,
    ref: dict[str, Any],
    *,
    task_id: str,
    attempt: int,
    plan_sha256: str,
    workspace_tree: str,
) -> ValidatedTaskEvidence:
    """验证 evidence 文件自身、所有来源 refs 与调用方冻结事实。"""
    root = Path(run_dir)
    expected_path = task_evidence_relative_path(task_id, attempt)
    if ref.get("path") != expected_path:
        raise TaskEvidenceValidationError(f"task evidence ref path 必须为 {expected_path}")
    path = _resolve_ref(root, ref)
    if not path.is_file() or _sha256_file(path) != ref.get("sha256"):
        raise TaskEvidenceValidationError("task evidence 文件缺失或自身 SHA-256 不匹配")
    try:
        value = _validate_schema(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskEvidenceValidationError(f"task evidence 不是合法 JSON: {exc}") from exc
    if path.read_bytes() != canonical_json_bytes(value):
        raise TaskEvidenceValidationError("task evidence 必须使用项目 canonical JSON 字节")
    expected = {
        "task_id": task_id,
        "attempt": attempt,
        "plan_sha256": plan_sha256,
        "workspace_tree": workspace_tree,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise TaskEvidenceValidationError(
                f"task evidence {key} 与冻结事实不一致"
            )
    _validate_source_refs(root, value)
    return ValidatedTaskEvidence(
        value=value,
        ref={"path": expected_path, "sha256": str(ref["sha256"])},
    )


def publish_task_evidence(
    run_dir: str | Path,
    *,
    task_id: str,
    attempt: int,
    plan_sha256: str,
    workspace_tree: str,
    build_result_refs: list[dict[str, str]],
    test_summary_refs: list[dict[str, str]],
) -> ValidatedTaskEvidence:
    """校验来源 receipts，并以不可变、幂等的 canonical 文件发布证据。"""
    root = Path(run_dir)
    relative = task_evidence_relative_path(task_id, attempt)
    path = root / relative
    value = _validate_schema(
        {
            "schema_version": "1.0",
            "task_id": task_id,
            "attempt": attempt,
            "plan_sha256": plan_sha256,
            "workspace_tree": workspace_tree,
            "build_result_refs": build_result_refs,
            "test_summary_refs": test_summary_refs,
        }
    )
    _validate_source_refs(root, value)
    payload = canonical_json_bytes(value)
    if path.exists():
        if path.read_bytes() != payload:
            raise TaskEvidenceValidationError(
                f"不可变 task evidence 已存在但内容不同: {relative}"
            )
    else:
        atomic_write_canonical_json(path, value)
    ref = {"path": relative, "sha256": _sha256_file(path)}
    return validate_task_evidence_ref(
        root,
        ref,
        task_id=task_id,
        attempt=attempt,
        plan_sha256=plan_sha256,
        workspace_tree=workspace_tree,
    )
