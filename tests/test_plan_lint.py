"""Plan v3 两级 plan_lint 单元测试（设计文档 5.2、5.2.5、6.4.5）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from nepa.speclib.lint import LintReport, plan_full_lint, plan_lint
from tests.plan_v3 import (
    INPUT_REFS,
    make_config_snapshot,
    make_constraints,
    make_link_result,
    make_manifest_tests,
    make_plan,
    make_spec,
)


def make_mini_input_refs() -> dict[str, dict[str, str]]:
    """构造当前 run 已冻结的四项输入引用。"""
    return deepcopy(INPUT_REFS)


def make_mini_plan() -> dict[str, Any]:
    """由真实 Linker 生成、通过 basic 与 full lint 的最小合法 Plan v3。"""
    return make_plan()


def _lint(plan: dict[str, Any]) -> LintReport:
    return plan_lint(
        plan,
        make_spec(),
        make_manifest_tests(),
        expected_input_refs=make_mini_input_refs(),
        config_snapshot=make_config_snapshot(),
    )


def _full_lint(
    plan: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
) -> LintReport:
    return plan_full_lint(
        plan,
        make_spec(),
        constraints=constraints or make_constraints(),
        blueprint=blueprint,
        tests_manifest=make_manifest_tests(),
        config_snapshot=make_config_snapshot(),
        expected_input_refs=make_mini_input_refs(),
    )


def _task(plan: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(task for task in plan["tasks"] if task["id"] == task_id)


def _package(plan: dict[str, Any], package_id: str) -> dict[str, Any]:
    return next(item for item in plan["work_packages"] if item["id"] == package_id)


def _contract(plan: dict[str, Any], contract_id: str) -> dict[str, Any]:
    return next(
        item for item in plan["architecture"]["contracts"] if item["id"] == contract_id
    )


def _coverage_test(plan: dict[str, Any], nodeid: str) -> dict[str, Any]:
    return next(row for row in plan["coverage"]["tests"] if row["nodeid"] == nodeid)


# ---------------------------------------------------------------------------
# 合法 fixture 全过
# ---------------------------------------------------------------------------


def test_valid_plan_passes_basic_lint() -> None:
    """合法 Plan v3 basic lint 0 error 且无警告（6.4 验收）。"""
    report = _lint(make_mini_plan())
    assert report.errors == []
    assert report.warnings == []


def test_valid_plan_passes_stage_full_lint() -> None:
    """同一 Plan 在 stage full lint 下也 0 error（5.2.5：S4 发布门）。"""
    result = make_link_result()
    report = _full_lint(result.plan, result.blueprint)
    assert report.errors == []
    assert report.warnings == []


def test_missing_manifest_downgrades_coverage_recompute_to_warning() -> None:
    """缺少清单/config snapshot 时 coverage 重算降级为警告，而非误报错误。"""
    report = plan_lint(make_mini_plan(), make_spec())
    assert report.errors == []
    assert "PLAN-COVERAGE-UNCHECKED" in report.warning_codes()


# ---------------------------------------------------------------------------
# 结构与冻结输入
# ---------------------------------------------------------------------------


def test_schema_violation_reports_plan_schema() -> None:
    """缺必填 input_refs（5.2.1）→ PLAN-SCHEMA。"""
    plan = make_mini_plan()
    del plan["input_refs"]
    assert "PLAN-SCHEMA" in _lint(plan).error_codes()


def test_frozen_input_mismatch_reports_input_mismatch() -> None:
    """Plan 引用与本次 run 冻结输入不一致 → PLAN-INPUT-MISMATCH。"""
    plan = make_mini_plan()
    plan["input_refs"]["language_profile"]["sha256"] = "ff" * 32
    mismatch = [i for i in _lint(plan).errors if i.code == "PLAN-INPUT-MISMATCH"]
    assert len(mismatch) == 1
    assert mismatch[0].path == "input_refs/language_profile/sha256"


def test_execution_state_field_is_hard_error() -> None:
    """Plan 携带 status/attempts/notes → PLAN-EXECUTION-STATE（5.2.5 硬错误）。"""
    plan = make_mini_plan()
    _task(plan, "T-001")["status"] = "pending"
    assert "PLAN-EXECUTION-STATE" in _lint(plan).error_codes()


def test_scaffold_task_is_hard_error() -> None:
    """Plan 中出现 scaffold task → PLAN-SCAFFOLD-TASK（脚手架只属于 S5）。"""
    plan = make_mini_plan()
    _task(plan, "T-001")["kind"] = "scaffold"
    assert "PLAN-SCAFFOLD-TASK" in _lint(plan).error_codes()


def test_legacy_top_level_modules_is_hard_error() -> None:
    """v2 顶层 modules 残留 → PLAN-LEGACY-MODULES。"""
    plan = make_mini_plan()
    plan["modules"] = []
    assert "PLAN-LEGACY-MODULES" in _lint(plan).error_codes()


def test_duplicate_task_id_reports_dup_id() -> None:
    """任务 id 重复 → PLAN-DUP-ID。"""
    plan = make_mini_plan()
    plan["tasks"].append(deepcopy(_task(plan, "T-002")))
    plan["tasks"][-1]["id"] = "T-001"
    assert "PLAN-DUP-ID" in _lint(plan).error_codes()


# ---------------------------------------------------------------------------
# 引用完整性与 DAG
# ---------------------------------------------------------------------------


def test_unknown_work_package_reports_ref_work_package() -> None:
    """任务指向未声明工作包 → PLAN-REF-WORK-PACKAGE。"""
    plan = make_mini_plan()
    _task(plan, "T-002")["work_package"] = "wp-missing"
    assert "PLAN-REF-WORK-PACKAGE" in _lint(plan).error_codes()


def test_unknown_module_reports_ref_module() -> None:
    """工作包指向未声明模块 → PLAN-REF-MODULE。"""
    plan = make_mini_plan()
    _package(plan, "wp-codec")["module"] = "nope"
    assert "PLAN-REF-MODULE" in _lint(plan).error_codes()


def test_unknown_contract_reports_ref_contract() -> None:
    """任务消费未声明 contract → PLAN-REF-CONTRACT。"""
    plan = make_mini_plan()
    _task(plan, "T-002")["consumes_contracts"].append("ghost-cli")
    assert "PLAN-REF-CONTRACT" in _lint(plan).error_codes()


def test_unknown_depends_on_reports_ref_task() -> None:
    """depends_on 引用不存在的任务 → PLAN-REF-TASK。"""
    plan = make_mini_plan()
    _task(plan, "T-004")["depends_on"] = ["T-999"]
    assert "PLAN-REF-TASK" in _lint(plan).error_codes()


def test_task_dependency_cycle_reports_plan_cycle() -> None:
    """任务 depends_on 成环 → PLAN-CYCLE。"""
    plan = make_mini_plan()
    _task(plan, "T-001")["depends_on"] = ["T-004"]
    assert "PLAN-CYCLE" in _lint(plan).error_codes()


def test_work_package_cycle_reports_plan_cycle() -> None:
    """工作包 depends_on 成环 → PLAN-CYCLE。"""
    plan = make_mini_plan()
    _package(plan, "wp-codec")["depends_on"] = ["wp-server"]
    assert "PLAN-CYCLE" in _lint(plan).error_codes()


def test_work_package_dependency_drift_reports_deps_error() -> None:
    """工作包 depends_on 不等于跨包 provider 集合 → PLAN-WORK-PACKAGE-DEPS。"""
    plan = make_mini_plan()
    _package(plan, "wp-client")["depends_on"] = []
    assert "PLAN-WORK-PACKAGE-DEPS" in _lint(plan).error_codes()


# ---------------------------------------------------------------------------
# 文件完整分区
# ---------------------------------------------------------------------------


def test_duplicate_deliverable_file_reports_conflict() -> None:
    """同一文件归两个任务 → PLAN-FILE-CONFLICT（v3 无脚手架头文件豁免）。"""
    plan = make_mini_plan()
    _task(plan, "T-002")["deliverable_files"].append("src/codec/codec_connect.c")
    assert "PLAN-FILE-CONFLICT" in _lint(plan).error_codes()


def test_task_file_union_must_equal_allowed_files() -> None:
    """任务文件并集 ≠ 工作包 allowed_files → PLAN-FILE-PARTITION。"""
    plan = make_mini_plan()
    task = _task(plan, "T-001")
    task["deliverable_files"] = ["src/codec/codec_connect.c"]
    assert "PLAN-FILE-PARTITION" in _lint(plan).error_codes()


def test_allowed_files_union_must_equal_module_owns_files() -> None:
    """工作包 allowed_files 并集 ≠ 模块 owns_files → PLAN-FILE-PARTITION。"""
    plan = make_mini_plan()
    _package(plan, "wp-codec")["allowed_files"] = ["src/codec/codec_connect.c"]
    assert "PLAN-FILE-PARTITION" in _lint(plan).error_codes()


def test_more_than_four_files_is_hard_error() -> None:
    """单任务 deliverable_files > 4 → PLAN-GRANULARITY 错误（5.2.5 硬错误）。"""
    plan = make_mini_plan()
    task = _task(plan, "T-001")
    task["deliverable_files"] = [f"src/extra_{index}.c" for index in range(5)]
    report = _lint(plan)
    assert "PLAN-GRANULARITY" in report.error_codes()


def test_task_writing_s5_frozen_file_reports_full_lint_error() -> None:
    """任务写入 s5_frozen 文件 → full lint 的 PLAN-FILE-FROZEN。"""
    result = make_link_result()
    plan = result.plan
    _task(plan, "T-001")["deliverable_files"].append("Makefile")
    assert "PLAN-FILE-FROZEN" in _full_lint(plan, result.blueprint).error_codes()


# ---------------------------------------------------------------------------
# contract 集合、provider 与闭包
# ---------------------------------------------------------------------------


def test_task_contract_union_must_equal_work_package_sets() -> None:
    """任务 contract 集合并集 ≠ 工作包集合 → PLAN-CONTRACT-SETS。"""
    plan = make_mini_plan()
    _package(plan, "wp-codec")["provides_contracts"] = []
    assert "PLAN-CONTRACT-SETS" in _lint(plan).error_codes()


def test_provider_task_id_must_match_unique_provider() -> None:
    """provider_task_id 与唯一提供任务不符 → PLAN-CONTRACT-PROVIDER。"""
    plan = make_mini_plan()
    _contract(plan, "codec-cli")["provider_task_id"] = "T-004"
    assert "PLAN-CONTRACT-PROVIDER" in _lint(plan).error_codes()


def test_s5_contract_must_not_be_provided_by_task() -> None:
    """任务提供 s5-ready contract → PLAN-CONTRACT-READY-GATE。"""
    plan = make_mini_plan()
    _task(plan, "T-001")["provides_contracts"].append("build-system")
    _package(plan, "wp-codec")["provides_contracts"].append("build-system")
    codec = next(
        item for item in plan["architecture"]["modules"] if item["id"] == "codec"
    )
    codec["provides_contracts"].append("build-system")
    assert "PLAN-CONTRACT-READY-GATE" in _lint(plan).error_codes()


def test_consumer_without_provider_ancestor_reports_closure_error() -> None:
    """contract 消费者缺少 provider ancestor → PLAN-CONTRACT-CLOSURE。"""
    plan = make_mini_plan()
    _task(plan, "T-002")["depends_on"] = []
    assert "PLAN-CONTRACT-CLOSURE" in _lint(plan).error_codes()


def test_provider_task_outside_owner_module_reports_owner_error() -> None:
    """provider task 不在 contract owner 模块内 → PLAN-CONTRACT-OWNER。"""
    plan = make_mini_plan()
    _contract(plan, "codec-cli")["owner"] = "server"
    assert "PLAN-CONTRACT-OWNER" in _lint(plan).error_codes()


# ---------------------------------------------------------------------------
# 责任守恒与 context slice
# ---------------------------------------------------------------------------


def test_task_claiming_out_of_scope_requirement_is_error() -> None:
    """任务认领所属工作包未获分配的需求 → PLAN-REQ-OUT-OF-SCOPE。"""
    plan = make_mini_plan()
    _task(plan, "T-003")["requirement_responsibilities"].append(
        {"req_id": "REQ-FRAME-001", "role": "supporting"}
    )
    assert "PLAN-REQ-OUT-OF-SCOPE" in _lint(plan).error_codes()


def test_unrefined_work_package_responsibility_is_error() -> None:
    """工作包责任未细化到任何任务 → PLAN-REQ-UNREFINED。"""
    plan = make_mini_plan()
    _task(plan, "T-001")["requirement_responsibilities"] = []
    assert "PLAN-REQ-UNREFINED" in _lint(plan).error_codes()


def test_requirement_without_primary_task_reports_uncovered() -> None:
    """非 DEFINITION 需求没有 primary 任务 → PLAN-REQ-UNCOVERED。"""
    plan = make_mini_plan()
    _package(plan, "wp-codec")["requirement_responsibilities"] = []
    _task(plan, "T-001")["requirement_responsibilities"] = []
    uncovered = [i for i in _lint(plan).errors if i.code == "PLAN-REQ-UNCOVERED"]
    assert uncovered
    assert "REQ-FRAME-001" in " ".join(issue.message for issue in uncovered)


def test_two_primary_tasks_for_one_requirement_reports_role_error() -> None:
    """同一需求出现多个 primary 任务 → PLAN-REQ-ROLE。"""
    plan = make_mini_plan()
    _package(plan, "wp-client")["requirement_responsibilities"].append(
        {"req_id": "REQ-FRAME-001", "role": "primary"}
    )
    _task(plan, "T-002")["requirement_responsibilities"].append(
        {"req_id": "REQ-FRAME-001", "role": "primary"}
    )
    _task(plan, "T-002")["context_refs"].append(
        {"kind": "requirement", "id": "REQ-FRAME-001"}
    )
    assert "PLAN-REQ-ROLE" in _lint(plan).error_codes()


def test_responsibility_missing_from_context_slice_is_error() -> None:
    """任务责任的 req_id 不在 context slice closure 中 → PLAN-REQ-CONTEXT。"""
    plan = make_mini_plan()
    _task(plan, "T-002")["context_refs"] = []
    assert "PLAN-REQ-CONTEXT" in _lint(plan).error_codes()


def test_unknown_context_ref_reports_ref_context() -> None:
    """context_ref 指向 spec 中不存在的元素 → PLAN-REF-CONTEXT。"""
    plan = make_mini_plan()
    _task(plan, "T-001")["context_refs"].append({"kind": "message", "id": "nonexistent"})
    assert "PLAN-REF-CONTEXT" in _lint(plan).error_codes()


# ---------------------------------------------------------------------------
# coverage 与 acceptance 反向注入
# ---------------------------------------------------------------------------


def test_coverage_task_binding_drift_reports_mismatch() -> None:
    """coverage 与重算结果不一致 → PLAN-COVERAGE-MISMATCH。"""
    plan = make_mini_plan()
    _coverage_test(plan, "tests/l1_codec/test_frame.py::test_roundtrip")["task_id"] = "T-004"
    assert "PLAN-COVERAGE-MISMATCH" in _lint(plan).error_codes()


def test_acceptance_tests_must_equal_enabled_task_gate_tests() -> None:
    """acceptance.tests ≠ coverage 绑定集合 → PLAN-ACCEPTANCE-TESTS。"""
    plan = make_mini_plan()
    _task(plan, "T-003")["acceptance"]["tests"] = [
        "tests/l1_codec/test_frame.py::test_roundtrip"
    ]
    assert "PLAN-ACCEPTANCE-TESTS" in _lint(plan).error_codes()


def test_disabled_test_must_not_enter_task_acceptance() -> None:
    """禁用测试不得进入任何任务 acceptance → PLAN-ACCEPTANCE-TESTS。"""
    plan = make_mini_plan()
    _task(plan, "T-004")["acceptance"]["tests"].append(
        "tests/l3_interop/test_reference.py::test_interop"
    )
    assert "PLAN-ACCEPTANCE-TESTS" in _lint(plan).error_codes()


def test_s5_gate_test_must_not_bind_a_task() -> None:
    """gate=s5 的 coverage 行携带 task_id → PLAN-TEST-GATE。"""
    plan = make_mini_plan()
    _coverage_test(plan, "tests/l0_build/test_scaffold.py::test_builds")["task_id"] = "T-001"
    assert "PLAN-TEST-GATE" in _lint(plan).error_codes()


def test_coverage_test_outside_manifest_reports_test_missing() -> None:
    """coverage 引用清单外测试 → PLAN-TEST-MISSING。"""
    plan = make_mini_plan()
    plan["coverage"]["tests"].append(
        {"nodeid": "tests/l9/test_x.py::test_y", "gate": "s7_only", "enabled": True, "task_id": None}
    )
    assert "PLAN-TEST-MISSING" in _lint(plan).error_codes()


# ---------------------------------------------------------------------------
# stage full lint 专属门禁
# ---------------------------------------------------------------------------


def test_blueprint_seal_mismatch_reports_full_lint_error() -> None:
    """顶层 blueprint seal 与本次 Blueprint 不一致 → PLAN-BLUEPRINT-HASH。"""
    result = make_link_result()
    result.plan["delivery_blueprint_sha256"] = "ab" * 32
    assert "PLAN-BLUEPRINT-HASH" in _full_lint(result.plan, result.blueprint).error_codes()


def test_full_lint_rejects_truncated_build_graph() -> None:
    result = make_link_result()
    constraints = make_constraints()
    constraints["build_artifacts"][0]["source_paths"] = []
    report = _full_lint(
        result.plan,
        result.blueprint,
        constraints=constraints,
    )
    assert "PLAN-DELIVERY-BUILD-GRAPH" in report.error_codes()


def test_full_lint_rejects_mechanical_contract_drift() -> None:
    result = make_link_result()
    blueprint = deepcopy(result.blueprint)
    blueprint["mechanical_generation_contracts"] = [{"id": "replacement"}]
    assert "PLAN-DELIVERY-MECHANICAL" in _full_lint(
        result.plan, blueprint
    ).error_codes()


def test_missing_required_build_variant_reports_build_gate() -> None:
    """任务未覆盖 required 构建变体 → PLAN-BUILD-GATE。"""
    result = make_link_result()
    _task(result.plan, "T-001")["acceptance"]["build_variant_ids"] = ["san"]
    assert "PLAN-BUILD-GATE" in _full_lint(result.plan, result.blueprint).error_codes()


def test_unknown_build_variant_reports_ref_error() -> None:
    """任务引用未声明构建变体 → PLAN-REF-BUILD-VARIANT。"""
    result = make_link_result()
    _task(result.plan, "T-001")["acceptance"]["build_variant_ids"].append("turbo")
    assert "PLAN-REF-BUILD-VARIANT" in _full_lint(result.plan, result.blueprint).error_codes()


def test_test_bound_before_contract_readiness_reports_readiness_error() -> None:
    """把测试绑到 contract 未就绪的任务 → PLAN-TEST-READINESS。"""
    result = make_link_result()
    plan = result.plan
    _coverage_test(plan, "tests/l2_behavior/test_connect.py::test_accepts")["task_id"] = "T-003"
    _task(plan, "T-003")["acceptance"]["tests"] = [
        "tests/l2_behavior/test_connect.py::test_accepts"
    ]
    _task(plan, "T-004")["acceptance"]["tests"] = []
    assert "PLAN-TEST-READINESS" in _full_lint(plan, result.blueprint).error_codes()


def test_budget_overflow_reports_plan_budget() -> None:
    """规划上下文超限 → PLAN-BUDGET。"""
    result = make_link_result()
    report = plan_full_lint(
        result.plan,
        make_spec(),
        constraints=make_constraints(),
        blueprint=result.blueprint,
        tests_manifest=make_manifest_tests(),
        config_snapshot=make_config_snapshot(),
        expected_input_refs=make_mini_input_refs(),
        planning_index={
            "preflight": {"fits": False, "required_tokens": 99, "context_limit": 10}
        },
    )
    assert "PLAN-BUDGET" in report.error_codes()
