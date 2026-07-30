"""M0-1 schema 转写的一致性测试（设计文档 5 章）。

逐一校验 nepa/schemas/examples/ 下的最小合法示例通过对应 schema，
并为 spec（5.1）与 plan（5.2）各写 2 个反例（缺必填、非法枚举）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SCHEMA_DIR: Path = Path(__file__).resolve().parent.parent / "nepa" / "schemas"
EXAMPLE_DIR: Path = SCHEMA_DIR / "examples"

# schema 文件名 -> 示例文件名（任务 A1 要求的 10 个工件 schema）
PAIRS: dict[str, str] = {
    "architecture-draft.schema.json": "architecture-draft.json",
    "artifact-manifest.schema.json": "artifact-manifest.json",
    "contract-map.schema.json": "contract-map.json",
    "flat-plan-draft.schema.json": "flat-plan-draft.json",
    "plan-critic.schema.json": "plan-critic.json",
    "s4-state.schema.json": "s4-state.json",
    "language-profile.schema.json": "language-profile.json",
    "specs-requirements.schema.json": "specs-requirements.json",
    "plan.schema.json": "plan.json",
    "plan-state.schema.json": "plan-state.json",
    "pending-round.schema.json": "pending-round.json",
    "segments.schema.json": "segments.json",
    "run.schema.json": "run.json",
    "spec-review.schema.json": "spec-review.json",
    "task-evidence.schema.json": "task-evidence.json",
    "task-shard.schema.json": "task-shard.json",
    "merge-decisions.schema.json": "merge-decisions.json",
    "test-summary.schema.json": "test-summary.json",
    "target-profile.schema.json": "target-profile.json",
    "test-bundle.schema.json": "test-bundle.json",
    "repair-log.schema.json": "repair-log.json",
    "report.schema.json": "report.json",
    "round-index.schema.json": "round-index.json",
    "tests-manifest.schema.json": "tests-manifest.json",
}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _validator(schema_name: str) -> Draft202012Validator:
    schema: dict[str, Any] = _load_json(SCHEMA_DIR / schema_name)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("schema_name", sorted(PAIRS))
def test_schema_is_valid_draft_2020_12(schema_name: str) -> None:
    """schema 自身必须是合法的 draft 2020-12 schema（5 章通用约定）。"""
    schema: dict[str, Any] = _load_json(SCHEMA_DIR / schema_name)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    # 所有工件顶层必须要求 schema_version（5 章通用约定）
    assert "schema_version" in schema["required"]


@pytest.mark.parametrize(("schema_name", "example_name"), sorted(PAIRS.items()))
def test_example_validates(schema_name: str, example_name: str) -> None:
    """每个最小示例必须通过对应 schema。"""
    validator = _validator(schema_name)
    instance = _load_json(EXAMPLE_DIR / example_name)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    messages = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
    assert not errors, f"{example_name} 未通过 {schema_name}:\n" + "\n".join(messages)


# ---------------------------------------------------------------------------
# Run v2 反例（5.6.2）
# ---------------------------------------------------------------------------


def _run_example() -> dict[str, Any]:
    return copy.deepcopy(_load_json(EXAMPLE_DIR / "run.json"))


def test_run_v2_spec_entry_rejects_doc_and_scope() -> None:
    bad = _run_example()
    bad["inputs"]["doc"] = {"path": "source.pdf", "sha256": "ab" * 32}
    bad["inputs"]["scope"] = {"path": "scope.yaml", "sha256": "cd" * 32}

    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(bad)


def test_run_v2_doc_entry_requires_doc_and_scope_and_forbids_spec() -> None:
    bad = _run_example()
    bad["entry"] = "doc-run"

    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(bad)

    good = _run_example()
    good["entry"] = "doc-run"
    del good["inputs"]["spec"]
    good["inputs"]["doc"] = {"path": "source.pdf", "sha256": "ab" * 32}
    good["inputs"]["scope"] = {"path": "scope.yaml", "sha256": "cd" * 32}
    _validator("run.schema.json").validate(good)


def test_run_v2_asset_paths_are_frozen_run_descriptions() -> None:
    bad = _run_example()
    bad["inputs"]["target_profile"]["path"] = "profiles/target.json"

    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(bad)


def test_run_v2_planned_stop_forbids_outcome() -> None:
    bad = _run_example()
    bad["outcome"] = "success"

    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(bad)


def _controlled_run() -> dict[str, Any]:
    value = _run_example()
    value["stages"]["s5"] = {
        "status": "failed",
        "started_at": "2026-07-26T14:36:00Z",
        "ended_at": "2026-07-26T14:37:30Z",
        "error": "Blueprint drift.",
    }
    value["termination_request"] = {
        "kind": "controlled_exit",
        "stage": "s5",
        "requested_at": "2026-07-26T14:37:30Z",
        "reason": {
            "code": "DELIVERY_BLUEPRINT_DRIFT",
            "detail": "S5 rejected a recomputed Delivery Blueprint.",
        },
    }
    value["termination_kind"] = "controlled_exit"
    value["outcome"] = "failed"
    value["exit_code"] = 20
    return value


def test_run_v2_controlled_exit_requires_request_and_rejects_success() -> None:
    missing = _controlled_run()
    del missing["termination_request"]
    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(missing)

    success = _controlled_run()
    success["outcome"] = "success"
    success["exit_code"] = 0
    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(success)


def test_run_v2_internal_error_may_retain_request() -> None:
    value = _controlled_run()
    value["termination_kind"] = "internal_error"
    del value["outcome"]
    value["exit_code"] = 1

    _validator("run.schema.json").validate(value)


@pytest.mark.parametrize("kind", ["completed", "planned_stop"])
def test_run_v2_completed_and_planned_stop_forbid_request(kind: str) -> None:
    value = _controlled_run()
    value["termination_kind"] = kind
    if kind == "completed":
        value["outcome"] = "failed"
        value["exit_code"] = 20
    else:
        del value["outcome"]
        value["exit_code"] = 0

    with pytest.raises(ValidationError):
        _validator("run.schema.json").validate(value)


# ---------------------------------------------------------------------------
# Report v2 availability envelope 反例（5.4）
# ---------------------------------------------------------------------------


def _report_example() -> dict[str, Any]:
    return copy.deepcopy(_load_json(EXAMPLE_DIR / "report.json"))


def test_report_v2_available_value_forbids_reason() -> None:
    bad = _report_example()
    bad["metrics"]["cost"]["total_usd"]["reason"] = {
        "code": "SHOULD_NOT_EXIST",
        "detail": "Available values cannot carry a reason.",
    }

    with pytest.raises(ValidationError):
        _validator("report.schema.json").validate(bad)


def test_report_v2_unavailable_value_requires_null_and_reason() -> None:
    bad = _report_example()
    bad["metrics"]["task_completion_rate"] = {
        "status": "unavailable",
        "value": 0,
    }

    with pytest.raises(ValidationError):
        _validator("report.schema.json").validate(bad)


def test_report_v2_unavailable_reason_code_is_machine_readable() -> None:
    bad = _report_example()
    bad["req_coverage"]["reason"]["code"] = "plan missing"

    with pytest.raises(ValidationError):
        _validator("report.schema.json").validate(bad)


def test_report_v2_available_artifact_forbids_reason() -> None:
    bad = _report_example()
    bad["artifact_availability"]["run"]["reason"] = {
        "code": "CONTRADICTION",
        "detail": "Available artifacts cannot carry a reason.",
    }

    with pytest.raises(ValidationError):
        _validator("report.schema.json").validate(bad)


# ---------------------------------------------------------------------------
# spec 反例（5.1）
# ---------------------------------------------------------------------------


def _spec_example() -> dict[str, Any]:
    return copy.deepcopy(_load_json(EXAMPLE_DIR / "specs-requirements.json"))


def test_spec_missing_required_requirements_fails() -> None:
    """缺必填顶层键 requirements（5.1.1 必填）必须校验失败。"""
    bad = _spec_example()
    del bad["requirements"]
    with pytest.raises(ValidationError):
        _validator("specs-requirements.schema.json").validate(bad)


def test_spec_illegal_level_enum_fails() -> None:
    """requirement.level 非法枚举值（5.1.6：MUST/MUST NOT/SHOULD/MAY）必须校验失败。"""
    bad = _spec_example()
    bad["requirements"][0]["level"] = "OPTIONAL"
    with pytest.raises(ValidationError):
        _validator("specs-requirements.schema.json").validate(bad)


# ---------------------------------------------------------------------------
# plan 反例（5.2）
# ---------------------------------------------------------------------------


def _plan_example() -> dict[str, Any]:
    return copy.deepcopy(_load_json(EXAMPLE_DIR / "plan.json"))


def test_plan_missing_required_input_refs_fails() -> None:
    """缺必填 input_refs（5.2：四项冻结输入路径 + 内容哈希）必须校验失败。"""
    bad = _plan_example()
    del bad["input_refs"]
    with pytest.raises(ValidationError):
        _validator("plan.schema.json").validate(bad)


def test_plan_illegal_task_kind_enum_fails() -> None:
    """task.kind 非法枚举值（5.2.2：v3 六值分类，已删除 scaffold）必须校验失败。"""
    bad = _plan_example()
    bad["tasks"][0]["kind"] = "scaffold"
    with pytest.raises(ValidationError):
        _validator("plan.schema.json").validate(bad)


def test_plan_execution_state_field_fails() -> None:
    """Plan v3 不得携带执行状态字段（5.2.5：status/attempts/notes 已移入 Plan State）。"""
    bad = _plan_example()
    bad["tasks"][0]["status"] = "pending"
    with pytest.raises(ValidationError):
        _validator("plan.schema.json").validate(bad)


def test_plan_s5_contract_with_provider_task_fails() -> None:
    """ready_gate=s5 的 contract 带 provider_task_id（5.2.1）必须校验失败。"""
    bad = _plan_example()
    contract = next(
        item for item in bad["architecture"]["contracts"] if item["ready_gate"] == "s5"
    )
    contract["provider_task_id"] = "T-001"
    with pytest.raises(ValidationError):
        _validator("plan.schema.json").validate(bad)


def test_plan_build_only_task_with_empty_tests_is_legal() -> None:
    """build-only 任务允许空 tests，但不得空 build_variant_ids（5.2.2）。"""
    good = _plan_example()
    good["tasks"][0]["acceptance"]["tests"] = []
    for row in good["coverage"]["tests"]:
        row["gate"] = "s7_only"
        row["task_id"] = None
    _validator("plan.schema.json").validate(good)
