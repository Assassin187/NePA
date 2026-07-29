"""Test Summary v2 的确定性聚合与 canonical producer（设计 5.4）。"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "test-summary.schema.json"
_RESULT_PRECEDENCE = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}


class TestSummaryValidationError(ValueError):
    """Test Summary v2 结构、聚合或冻结绑定不合法。"""


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


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
        raise TestSummaryValidationError(detail)
    if not isinstance(value, dict):
        raise TestSummaryValidationError("Test Summary 顶层必须为 object")
    return value


def aggregate_req_matrix(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 error > fail > skipped > pass 聚合 requirement 结果。"""
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        for req_id in case.get("req_ids", []):
            if isinstance(req_id, str):
                grouped[req_id].append(case)
    matrix: list[dict[str, Any]] = []
    for req_id, related in sorted(grouped.items()):
        result = max(
            (str(case.get("result")) for case in related),
            key=lambda item: _RESULT_PRECEDENCE.get(item, 99),
        )
        matrix.append(
            {
                "req_id": req_id,
                "tests": sorted({str(case["nodeid"]) for case in related}),
                "result": result,
            }
        )
    return matrix


def validate_test_summary(value: Any) -> dict[str, Any]:
    """执行 Schema 与可重算聚合/唯一性语义校验。"""
    summary = _validate_schema(value)
    round_id = summary["round_id"]
    parent = summary["parent_round_id"]
    if parent is not None and parent >= round_id:
        raise TestSummaryValidationError("parent_round_id 必须小于 round_id")
    variant_ids = [item["variant_id"] for item in summary["build_results"]]
    if len(variant_ids) != len(set(variant_ids)):
        raise TestSummaryValidationError("build_results.variant_id 不得重复")
    nodeids = [item["nodeid"] for item in summary["cases"]]
    if len(nodeids) != len(set(nodeids)):
        raise TestSummaryValidationError("cases.nodeid 不得重复")
    expected_matrix = aggregate_req_matrix(summary["cases"])
    if summary["req_matrix"] != expected_matrix:
        raise TestSummaryValidationError("req_matrix 与 cases 的确定性聚合不一致")
    return summary


def build_test_summary(
    *,
    round_id: int,
    trigger: str,
    workspace_head: str,
    workspace_tree: str,
    parent_round_id: int | None,
    plan_sha256: str,
    delivery_blueprint_sha256: str,
    manifest_sha256: str,
    bundle_tree_sha256: str,
    build_results: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    task_id: str | None = None,
    attempt: int | None = None,
    repair_id: str | None = None,
) -> dict[str, Any]:
    """从冻结上下文与原始结果构造、重算并校验 Summary v2。"""
    value: dict[str, Any] = {
        "schema_version": "2.0",
        "round_id": round_id,
        "trigger": trigger,
        "workspace_head": workspace_head,
        "workspace_tree": workspace_tree,
        "parent_round_id": parent_round_id,
        "plan_sha256": plan_sha256,
        "delivery_blueprint_sha256": delivery_blueprint_sha256,
        "manifest_sha256": manifest_sha256,
        "bundle_tree_sha256": bundle_tree_sha256,
        "build_results": build_results,
        "cases": cases,
        "req_matrix": aggregate_req_matrix(cases),
    }
    for name, optional in (
        ("task_id", task_id),
        ("attempt", attempt),
        ("repair_id", repair_id),
    ):
        if optional is not None:
            value[name] = optional
    return validate_test_summary(value)
