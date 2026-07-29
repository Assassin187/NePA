"""Test Summary v2 deterministic producer 测试（设计 5.4）。"""

from __future__ import annotations

from typing import Any

import pytest

from nepa.test_summary import (
    TestSummaryValidationError as SummaryValidationError,
)
from nepa.test_summary import (
    aggregate_req_matrix,
    build_test_summary,
    validate_test_summary,
)

HASHES = {
    "plan_sha256": "ab" * 32,
    "delivery_blueprint_sha256": "bc" * 32,
    "manifest_sha256": "cd" * 32,
    "bundle_tree_sha256": "de" * 32,
}


def _builds() -> list[dict[str, Any]]:
    return [
        {
            "variant_id": "release",
            "result": "pass",
            "duration_ms": 100.0,
            "warnings": 0,
            "errors": 0,
        }
    ]


def _cases() -> list[dict[str, Any]]:
    return [
        {
            "nodeid": "tests/test_codec.py::test_ok",
            "layer": "l1",
            "result": "pass",
            "duration_ms": 5.0,
            "req_ids": ["REQ-FRAME-001"],
        },
        {
            "nodeid": "tests/test_codec.py::test_bad",
            "layer": "l1",
            "result": "skipped",
            "duration_ms": 0.0,
            "output_excerpt": "runtime precondition unavailable",
            "req_ids": ["REQ-FRAME-001", "REQ-FRAME-002"],
        },
    ]


def _summary(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "round_id": 2,
        "trigger": "s6_task",
        "task_id": "T-001",
        "attempt": 1,
        "workspace_head": "a" * 40,
        "workspace_tree": "b" * 40,
        "parent_round_id": 1,
        "build_results": _builds(),
        "cases": _cases(),
        **HASHES,
    }
    values.update(overrides)
    return build_test_summary(**values)


def test_req_matrix_is_recomputed_with_conservative_precedence() -> None:
    matrix = aggregate_req_matrix(_cases())

    assert matrix == [
        {
            "req_id": "REQ-FRAME-001",
            "tests": [
                "tests/test_codec.py::test_bad",
                "tests/test_codec.py::test_ok",
            ],
            "result": "skipped",
        },
        {
            "req_id": "REQ-FRAME-002",
            "tests": ["tests/test_codec.py::test_bad"],
            "result": "skipped",
        },
    ]


def test_build_only_s6_task_has_empty_cases_and_matrix() -> None:
    summary = _summary(cases=[])

    assert summary["cases"] == []
    assert summary["req_matrix"] == []
    validate_test_summary(summary)


def test_trigger_context_is_strictly_bound() -> None:
    with pytest.raises(SummaryValidationError):
        _summary(task_id=None, attempt=None)
    with pytest.raises(SummaryValidationError):
        _summary(trigger="s7_full")

    s7 = _summary(
        trigger="s7_full",
        task_id=None,
        attempt=None,
    )
    assert "task_id" not in s7 and "attempt" not in s7

    repair = _summary(
        trigger="s8_regression",
        task_id=None,
        attempt=None,
        repair_id="R-001",
    )
    assert repair["repair_id"] == "R-001"


def test_pass_build_forbids_warning_or_error_counts() -> None:
    bad = _builds()
    bad[0]["warnings"] = 1

    with pytest.raises(SummaryValidationError):
        _summary(build_results=bad)


def test_parent_round_must_precede_current_round() -> None:
    with pytest.raises(SummaryValidationError, match="parent_round_id"):
        _summary(parent_round_id=2)


def test_duplicate_variants_and_nodeids_are_rejected() -> None:
    with pytest.raises(SummaryValidationError, match="variant_id"):
        _summary(build_results=[*_builds(), *_builds()])
    with pytest.raises(SummaryValidationError, match="nodeid"):
        _summary(cases=[*_cases(), _cases()[0]])


def test_caller_supplied_req_matrix_cannot_drift() -> None:
    summary = _summary()
    summary["req_matrix"][0]["result"] = "pass"

    with pytest.raises(SummaryValidationError, match="req_matrix"):
        validate_test_summary(summary)
