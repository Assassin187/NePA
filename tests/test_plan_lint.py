"""plan_lint 单元测试（设计文档 5.2 / 6.4）。"""

from __future__ import annotations

from typing import Any

from nepa.speclib.lint import LintReport, plan_lint
from tests.test_spec_lint import make_mini_manifest, make_mini_spec


def make_mini_plan() -> dict[str, Any]:
    """构造与 make_mini_spec 一致、通过全部 plan_lint 检查的最小合法计划。"""
    return {
        "schema_version": "1.0",
        "spec_ref": {"path": "spec/spec.json", "sha256": "ab" * 32},
        "modules": [
            {
                "id": "core",
                "name": "核心实现",
                "purpose": "M0 最小实现（编解码 + 会话状态机）",
                "owns_files": [
                    "Makefile",
                    "include/mqtt/mqtt_codec.h",
                    "src/mqtt_codec.c",
                    "src/mqtt_session.c",
                ],
            }
        ],
        "tasks": [
            {
                "id": "T-001",
                "title": "项目脚手架",
                "goal": "生成可构建的空项目与接口头文件",
                "kind": "scaffold",
                "module": "core",
                "instructions": "按 7.2 布局生成 Makefile 与接口头文件。",
                "deliverable_files": ["Makefile", "include/mqtt/mqtt_codec.h"],
                "context_refs": [],
                "depends_on": [],
                "acceptance": {"build": True, "tests": []},
                "status": "pending",
                "attempts": 0,
                "notes": "",
            },
            {
                "id": "T-002",
                "title": "CONNECT/CONNACK 编解码",
                "goal": "CONNECT 编解码通过 L1 往返测试",
                "kind": "codec",
                "module": "core",
                "instructions": "实现 connect/connack 编解码；remaining_length 派生计算。",
                "deliverable_files": ["src/mqtt_codec.c", "include/mqtt/mqtt_codec.h"],
                "context_refs": [
                    {"kind": "message", "id": "connect"},
                    {"kind": "message", "id": "connack"},
                    {"kind": "type", "id": "mqtt_varint"},
                    {"kind": "interface_file", "id": "include/mqtt/mqtt_codec.h"},
                ],
                "depends_on": ["T-001"],
                "acceptance": {
                    "build": True,
                    "tests": [
                        "l1_codec/test_varint.py::test_remaining_length_roundtrip",
                        "l2_behavior/test_connect.py::test_bad_protocol_level",
                    ],
                },
                "status": "pending",
                "attempts": 0,
                "notes": "",
            },
            {
                "id": "T-003",
                "title": "broker 会话状态机",
                "goal": "会话状态机通过 L2 行为测试",
                "kind": "state",
                "module": "core",
                "instructions": "实现 broker_session 状态机与 QoS0 转发行为。",
                "deliverable_files": ["src/mqtt_session.c"],
                "context_refs": [
                    {"kind": "state_machine", "id": "broker_session"},
                    {"kind": "behavior", "id": "BEH-BROKER-001"},
                ],
                "depends_on": ["T-002"],
                "acceptance": {
                    "build": True,
                    "tests": [
                        "l2_behavior/test_connect.py::test_second_connect_closes",
                        "l2_behavior/test_publish.py::test_qos0_forward",
                    ],
                },
                "status": "pending",
                "attempts": 0,
                "notes": "",
            },
        ],
    }


def _lint(plan: dict[str, Any]) -> LintReport:
    return plan_lint(plan, make_mini_spec(), make_mini_manifest())


# ---------------------------------------------------------------------------
# 合法 fixture 全过
# ---------------------------------------------------------------------------


def test_valid_mini_plan_passes() -> None:
    """合法 mini-plan 0 error 且无警告（6.4 验收：plan_lint 0 error）。"""
    report = _lint(make_mini_plan())
    assert report.errors == []
    assert report.warnings == []


def test_scaffold_shared_header_is_exempt_from_exclusivity() -> None:
    """接口头文件由脚手架任务与实现任务共同持有时豁免互斥（5.2/6.4）。"""
    report = _lint(make_mini_plan())
    assert "PLAN-FILE-CONFLICT" not in report.error_codes()


def test_manifest_omitted_skips_acceptance_check() -> None:
    """未提供 tests_manifest 时跳过 acceptance 存在性检查（最保守降级）。"""
    report = plan_lint(make_mini_plan(), make_mini_spec())
    assert report.errors == []


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------


def test_schema_violation_reports_plan_schema() -> None:
    """缺必填 spec_ref（5.2）→ PLAN-SCHEMA。"""
    plan = make_mini_plan()
    del plan["spec_ref"]
    report = _lint(plan)
    assert "PLAN-SCHEMA" in report.error_codes()


# ---------------------------------------------------------------------------
# DAG 无环
# ---------------------------------------------------------------------------


def test_dependency_cycle_reports_plan_cycle() -> None:
    """depends_on 成环 → PLAN-CYCLE（5.2：depends_on 构成 DAG）。"""
    plan = make_mini_plan()
    plan["tasks"][0]["depends_on"] = ["T-003"]  # T-001 -> T-003 -> T-002 -> T-001
    report = _lint(plan)
    assert "PLAN-CYCLE" in report.error_codes()


def test_unknown_depends_on_reports_ref_task() -> None:
    """depends_on 引用不存在的任务 → PLAN-REF-TASK。"""
    plan = make_mini_plan()
    plan["tasks"][2]["depends_on"] = ["T-999"]
    report = _lint(plan)
    assert "PLAN-REF-TASK" in report.error_codes()


# ---------------------------------------------------------------------------
# deliverable_files 互斥
# ---------------------------------------------------------------------------


def test_duplicate_deliverable_file_reports_conflict() -> None:
    """同一 .c 文件归两个任务 → PLAN-FILE-CONFLICT。"""
    plan = make_mini_plan()
    plan["tasks"][2]["deliverable_files"].append("src/mqtt_codec.c")
    report = _lint(plan)
    assert "PLAN-FILE-CONFLICT" in report.error_codes()


def test_shared_header_without_scaffold_holder_reports_conflict() -> None:
    """共享头文件但无脚手架任务持有 → 不豁免，报 PLAN-FILE-CONFLICT。"""
    plan = make_mini_plan()
    plan["tasks"][0]["kind"] = "codec"  # 脚手架任务变身，豁免条件不再成立
    report = _lint(plan)
    assert "PLAN-FILE-CONFLICT" in report.error_codes()


# ---------------------------------------------------------------------------
# acceptance 测试存在性
# ---------------------------------------------------------------------------


def test_acceptance_test_missing_from_manifest_reports_test_missing() -> None:
    """acceptance 引用清单外测试 → PLAN-TEST-MISSING（6.4）。"""
    plan = make_mini_plan()
    plan["tasks"][1]["acceptance"]["tests"].append("l9_missing/test_x.py::test_y")
    report = _lint(plan)
    assert "PLAN-TEST-MISSING" in report.error_codes()


# ---------------------------------------------------------------------------
# 粒度约束（SHOULD → warning）
# ---------------------------------------------------------------------------


def test_more_than_four_files_reports_granularity_warning() -> None:
    """单任务 deliverable_files > 4 → PLAN-GRANULARITY 警告（5.2 SHOULD 级）。"""
    plan = make_mini_plan()
    plan["tasks"][1]["deliverable_files"] = [f"src/extra_{i}.c" for i in range(5)]
    report = _lint(plan)
    assert "PLAN-GRANULARITY" in report.warning_codes()
    assert "PLAN-GRANULARITY" not in report.error_codes()


# ---------------------------------------------------------------------------
# MUST 需求可追溯
# ---------------------------------------------------------------------------


def test_must_req_untraceable_reports_req_uncovered() -> None:
    """去掉 T-003 的 context_refs 后，REQ-STATE-001/REQ-PUB-001 不可追溯 → PLAN-REQ-UNCOVERED。"""
    plan = make_mini_plan()
    plan["tasks"][2]["context_refs"] = []
    report = _lint(plan)
    uncovered = [i for i in report.errors if i.code == "PLAN-REQ-UNCOVERED"]
    assert uncovered
    mentioned = " ".join(i.message for i in uncovered)
    assert "REQ-STATE-001" in mentioned
    assert "REQ-PUB-001" in mentioned


def test_unknown_context_ref_reports_ref_context() -> None:
    """context_ref 指向 spec 中不存在的元素 → PLAN-REF-CONTEXT。"""
    plan = make_mini_plan()
    plan["tasks"][1]["context_refs"].append({"kind": "message", "id": "nonexistent"})
    report = _lint(plan)
    assert "PLAN-REF-CONTEXT" in report.error_codes()


def test_duplicate_task_id_reports_dup_id() -> None:
    """任务 id 重复（5 章通用约定：唯一性由校验器检查）→ PLAN-DUP-ID。"""
    plan = make_mini_plan()
    plan["tasks"][2]["id"] = "T-002"
    report = _lint(plan)
    assert "PLAN-DUP-ID" in report.error_codes()
