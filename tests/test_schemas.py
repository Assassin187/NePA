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
    "specs-requirements.schema.json": "specs-requirements.json",
    "plan.schema.json": "plan.json",
    "segments.schema.json": "segments.json",
    "run.schema.json": "run.json",
    "spec-review.schema.json": "spec-review.json",
    "merge-decisions.schema.json": "merge-decisions.json",
    "test-summary.schema.json": "test-summary.json",
    "repair-log.schema.json": "repair-log.json",
    "report.schema.json": "report.json",
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


def test_plan_illegal_status_enum_fails() -> None:
    """task.status 非法枚举值（5.2：含 blocked_by_dependency 的五值枚举）必须校验失败。"""
    bad = _plan_example()
    bad["tasks"][0]["status"] = "cancelled"
    with pytest.raises(ValidationError):
        _validator("plan.schema.json").validate(bad)


def test_plan_status_blocked_by_dependency_is_legal() -> None:
    """blocked_by_dependency 是合法状态（5.2 / 6.6.1）。"""
    good = _plan_example()
    good["tasks"][0]["status"] = "blocked_by_dependency"
    _validator("plan.schema.json").validate(good)
