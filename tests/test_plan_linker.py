"""确定性 Linker 单元测试（设计 6.4.5 九步、5.2.2、5.2.3）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from nepa.canonical import canonical_sha256
from nepa.delivery import compile_delivery_constraints
from nepa.plan_draft import (
    LinkResult,
    PlanDraftError,
    build_coverage,
    enabled_test_nodeids,
    link_plan_draft,
    normalize_flat_draft,
    normalize_layered_draft,
)
from tests.plan_v3 import (
    INPUT_REFS,
    example,
    make_config_snapshot,
    make_constraints,
    make_draft,
    make_link_result,
    make_manifest,
    make_manifest_tests,
    make_shards,
    make_spec,
)


def _shard(shards: list[dict[str, Any]], work_package_id: str) -> dict[str, Any]:
    return next(item for item in shards if item["work_package_id"] == work_package_id)


def _task(shards: list[dict[str, Any]], work_package_id: str) -> dict[str, Any]:
    return _shard(shards, work_package_id)["tasks"][0]


def _link(
    *,
    shards: list[dict[str, Any]] | None = None,
    architecture_draft: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    config_snapshot: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
) -> LinkResult:
    draft = normalize_layered_draft(
        architecture_draft
        if architecture_draft is not None
        else example("architecture-draft.json"),
        shards if shards is not None else make_shards(),
    )
    spec_value = spec if spec is not None else make_spec()
    manifest_value = manifest if manifest is not None else make_manifest()
    if constraints is None:
        constraints = compile_delivery_constraints(
            spec_value,
            example("target-profile.json"),
            example("language-profile.json"),
            example("test-bundle.json"),
            manifest_value,
        )
    return link_plan_draft(
        draft,
        spec=spec_value,
        manifest=manifest_value,
        constraints=constraints,
        input_refs=deepcopy(INPUT_REFS),
        config_snapshot=config_snapshot if config_snapshot is not None else make_config_snapshot(),
        review=review,
    )


def _coverage(
    tasks: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    providers: dict[str, str] | None = None,
) -> dict[str, Any]:
    plan = make_link_result().plan
    return build_coverage(
        tasks,
        spec=make_spec(),
        manifest=manifest,
        contracts=plan["architecture"]["contracts"],
        providers=providers
        if providers is not None
        else {
            item["id"]: item["provider_task_id"]
            for item in plan["architecture"]["contracts"]
            if "provider_task_id" in item
        },
        work_package_by_task={task["id"]: task["work_package"] for task in tasks},
        config_snapshot=make_config_snapshot(),
    )


# ---------------------------------------------------------------------------
# 规范化：layered
# ---------------------------------------------------------------------------


def test_layered_draft_normalizes_every_work_package_shard() -> None:
    """冻结 architecture + 每包一个 shard → 同一 PlanDraftIR（6.4.3）。"""
    draft = make_draft()
    assert sorted(draft.tasks_by_work_package) == [
        "wp-client",
        "wp-codec",
        "wp-server",
        "wp-transport",
    ]
    assert [item["id"] for item in draft.work_packages] == [
        "wp-codec",
        "wp-client",
        "wp-transport",
        "wp-server",
    ]


def test_layered_draft_without_architecture_is_rejected() -> None:
    with pytest.raises(PlanDraftError, match="architecture"):
        normalize_layered_draft({"work_packages": []}, [])


def test_layered_draft_rejects_duplicate_shard_for_one_work_package() -> None:
    shards = make_shards()
    shards.append(deepcopy(_shard(shards, "wp-codec")))
    with pytest.raises(PlanDraftError, match="多份 shard"):
        normalize_layered_draft(example("architecture-draft.json"), shards)


def test_layered_draft_rejects_shard_for_unknown_work_package() -> None:
    shards = make_shards()
    _shard(shards, "wp-codec")["work_package_id"] = "wp-ghost"
    with pytest.raises(PlanDraftError, match="未知工作包 wp-ghost"):
        normalize_layered_draft(example("architecture-draft.json"), shards)


def test_layered_draft_rejects_missing_shard() -> None:
    shards = [item for item in make_shards() if item["work_package_id"] != "wp-server"]
    with pytest.raises(PlanDraftError, match="缺少 shard"):
        normalize_layered_draft(example("architecture-draft.json"), shards)


# ---------------------------------------------------------------------------
# 规范化：flat
# ---------------------------------------------------------------------------


def _flat_draft() -> dict[str, Any]:
    architecture_draft = example("architecture-draft.json")
    tasks: list[dict[str, Any]] = []
    for shard in make_shards():
        for task in shard["tasks"]:
            tasks.append({**deepcopy(task), "work_package_id": shard["work_package_id"]})
    return {
        "architecture": architecture_draft["architecture"],
        "work_packages": architecture_draft["work_packages"],
        "tasks": tasks,
    }


def test_flat_draft_groups_tasks_into_the_same_ir() -> None:
    """flat 策略只是分组方式不同，链接语义与 layered 完全共享（6.4.3）。"""
    flat = normalize_flat_draft(_flat_draft())
    layered = make_draft()
    assert flat.tasks_by_work_package == layered.tasks_by_work_package
    assert flat.architecture == layered.architecture


def test_flat_draft_and_layered_draft_link_to_the_same_plan() -> None:
    flat_plan = link_plan_draft(
        normalize_flat_draft(_flat_draft()),
        spec=make_spec(),
        manifest=make_manifest(),
        constraints=make_constraints(),
        input_refs=deepcopy(INPUT_REFS),
        config_snapshot=make_config_snapshot(),
    ).plan
    assert flat_plan == make_link_result().plan


def test_flat_draft_rejects_task_pointing_at_unknown_work_package() -> None:
    draft = _flat_draft()
    draft["tasks"][0]["work_package_id"] = "wp-ghost"
    with pytest.raises(PlanDraftError, match="未知工作包"):
        normalize_flat_draft(draft)


def test_flat_draft_rejects_work_package_without_tasks() -> None:
    draft = _flat_draft()
    draft["tasks"] = [
        task for task in draft["tasks"] if task["work_package_id"] != "wp-transport"
    ]
    with pytest.raises(PlanDraftError, match="没有任务"):
        normalize_flat_draft(draft)


# ---------------------------------------------------------------------------
# 步骤 1：集合等式与责任细化
# ---------------------------------------------------------------------------


def test_task_files_must_exactly_partition_allowed_files() -> None:
    shards = make_shards()
    _task(shards, "wp-codec")["deliverable_files"] = ["src/codec/codec_connect.c"]
    with pytest.raises(PlanDraftError, match="恰等于 allowed_files"):
        _link(shards=shards)


def test_task_contract_union_must_equal_work_package_contracts() -> None:
    shards = make_shards()
    _task(shards, "wp-client")["consumes_contracts"] = []
    with pytest.raises(PlanDraftError, match="consumes_contracts"):
        _link(shards=shards)


def test_task_cannot_claim_responsibility_outside_its_work_package() -> None:
    shards = make_shards()
    _task(shards, "wp-transport")["requirement_responsibilities"] = [
        {"req_id": "REQ-FRAME-001", "role": "supporting"}
    ]
    with pytest.raises(PlanDraftError, match="包外责任 REQ-FRAME-001"):
        _link(shards=shards)


def test_work_package_responsibility_must_be_refined_into_a_task() -> None:
    shards = make_shards()
    _task(shards, "wp-codec")["requirement_responsibilities"] = []
    with pytest.raises(PlanDraftError, match="未细化到任何任务"):
        _link(shards=shards)


# ---------------------------------------------------------------------------
# 步骤 2：provider 解析与跨包依赖边
# ---------------------------------------------------------------------------


def test_contract_needs_exactly_one_provider_task() -> None:
    architecture_draft = example("architecture-draft.json")
    package = next(
        item for item in architecture_draft["work_packages"] if item["id"] == "wp-transport"
    )
    package["provides_contracts"] = ["core-transport", "codec-cli"]
    shards = make_shards()
    _task(shards, "wp-transport")["provides_contracts"] = ["core-transport", "codec-cli"]
    with pytest.raises(PlanDraftError, match="codec-cli 必须恰有一个 provider task"):
        _link(shards=shards, architecture_draft=architecture_draft)


def test_provider_task_cannot_consume_its_own_contract() -> None:
    architecture_draft = example("architecture-draft.json")
    package = next(
        item for item in architecture_draft["work_packages"] if item["id"] == "wp-codec"
    )
    package["consumes_contracts"] = ["codec-cli"]
    shards = make_shards()
    _task(shards, "wp-codec")["consumes_contracts"] = ["codec-cli"]
    with pytest.raises(PlanDraftError, match="不得同时消费"):
        _link(shards=shards, architecture_draft=architecture_draft)


def test_consumer_tasks_get_derived_cross_package_edges() -> None:
    """跨包边只能由 contract 关系派生，shard 无法自行声明（6.4.5 步骤 2）。"""
    result = make_link_result()
    edges = {task["id"]: task["depends_on"] for task in result.plan["tasks"]}
    assert edges == {
        "T-001": [],
        "T-002": ["T-001"],
        "T-003": [],
        "T-004": ["T-001", "T-003"],
    }
    assert result.link_report["contract_provider_task_ids"] == {
        "client-cli": "T-002",
        "codec-cli": "T-001",
        "core-transport": "T-003",
        "server-process": "T-004",
    }


def test_local_dependency_cannot_name_another_work_package_task() -> None:
    shards = make_shards()
    _task(shards, "wp-server")["depends_on"] = ["codec"]
    with pytest.raises(PlanDraftError, match="unknown local dependency"):
        _link(shards=shards)


def test_work_package_dependencies_must_equal_derived_provider_packages() -> None:
    architecture_draft = example("architecture-draft.json")
    package = next(
        item for item in architecture_draft["work_packages"] if item["id"] == "wp-server"
    )
    package["depends_on"] = ["wp-codec"]
    with pytest.raises(PlanDraftError, match="wp-server: depends_on"):
        _link(architecture_draft=architecture_draft)


def test_provider_work_package_is_replaced_by_provider_task_in_final_plan() -> None:
    """架构草稿只预留 provider 工作包；Plan 只保留唯一 provider task。"""
    contracts = {
        item["id"]: item for item in make_link_result().plan["architecture"]["contracts"]
    }
    assert "provider_work_package_id" not in contracts["codec-cli"]
    assert contracts["codec-cli"]["provider_task_id"] == "T-001"
    assert contracts["build-system"]["ready_gate"] == "s5"
    assert "provider_task_id" not in contracts["build-system"]


# ---------------------------------------------------------------------------
# 步骤 5/6：context_refs 与 coverage
# ---------------------------------------------------------------------------


def test_requirement_context_refs_are_injected_from_responsibilities() -> None:
    plan = make_link_result().plan
    codec = next(task for task in plan["tasks"] if task["id"] == "T-001")
    assert {"kind": "requirement", "id": "REQ-FRAME-001"} in codec["context_refs"]
    assert {"kind": "message", "id": "connect"} in codec["context_refs"]
    transport = next(task for task in plan["tasks"] if task["id"] == "T-003")
    assert transport["context_refs"] == [
        {"kind": "interface_file", "id": "include/proto/core_transport.h"}
    ]


def test_work_package_requirement_refs_are_not_duplicated() -> None:
    """示例工作包已声明 requirement ref，注入必须幂等。"""
    plan = make_link_result().plan
    package = next(item for item in plan["work_packages"] if item["id"] == "wp-codec")
    refs = [ref for ref in package["context_refs"] if ref["kind"] == "requirement"]
    assert refs == [{"kind": "requirement", "id": "REQ-FRAME-001"}]


def test_task_gate_binds_to_earliest_legal_task_in_topological_order() -> None:
    """gate=task 绑定到闭包首个合法任务；s5 契约不参与闭包（5.2.3）。"""
    manifest = make_manifest()
    manifest["tests"].append(
        {
            "nodeid": "tests/l2_behavior/test_pipeline.py::test_end_to_end",
            "description": "publish one message through the codec and client CLIs",
            "layer": "l2",
            "req_ids": ["REQ-PUBLISH-001"],
            "gate": "task",
            "required_contracts": ["build-system", "codec-cli", "client-cli"],
        }
    )
    rows = {
        row["nodeid"]: row["task_id"]
        for row in _link(manifest=manifest).plan["coverage"]["tests"]
    }
    # 需要 {T-001, T-002}：s5 契约 build-system 不参与闭包，绑定停在 T-002 而非末尾任务。
    assert rows["tests/l2_behavior/test_pipeline.py::test_end_to_end"] == "T-002"
    assert rows["tests/l1_codec/test_frame.py::test_roundtrip"] == "T-001"
    assert rows["tests/l0_build/test_scaffold.py::test_builds"] is None


def test_task_gate_without_any_legal_closure_is_rejected() -> None:
    """要求两个互不可达任务的测试无法确定性绑定 → 拒绝而非猜测。"""
    manifest = make_manifest()
    manifest["tests"].append(
        {
            "nodeid": "tests/l2_behavior/test_cross.py::test_client_and_server",
            "description": "client CLI plus server requirement",
            "layer": "l2",
            "req_ids": ["REQ-CONNECT-001"],
            "gate": "task",
            "required_contracts": ["client-cli"],
        }
    )
    with pytest.raises(PlanDraftError, match="不存在同时满足 contract 与 REQ 闭包"):
        _link(manifest=manifest)


def test_task_gate_requires_a_provider_task_for_every_required_contract() -> None:
    """provider 缺失时不得回退到任意任务：coverage 必须拒绝（5.2.3）。"""
    tasks = deepcopy(make_link_result().plan["tasks"])
    with pytest.raises(PlanDraftError, match="contract codec-cli 没有 provider task"):
        _coverage(tasks, make_manifest(), providers={})


def test_non_definition_requirement_needs_exactly_one_primary_task() -> None:
    tasks = deepcopy(make_link_result().plan["tasks"])
    for task in tasks:
        if task["id"] == "T-001":
            task["requirement_responsibilities"] = [
                {"req_id": "REQ-FRAME-001", "role": "supporting"}
            ]
    manifest = make_manifest()
    manifest["tests"] = [
        item for item in manifest["tests"] if item["gate"] != "task"
    ]
    with pytest.raises(PlanDraftError, match="REQ-FRAME-001: 非 DEFINITION"):
        _coverage(tasks, manifest)


def test_normative_requirement_cannot_be_covered_only_at_s5() -> None:
    """MUST/MUST NOT 的验证不得仅由脚手架门替代。"""
    manifest = make_manifest()
    for entry in manifest["tests"]:
        if entry["nodeid"] == "tests/l1_codec/test_frame.py::test_roundtrip":
            entry["gate"] = "s5"
            entry["required_contracts"] = ["build-system"]
    with pytest.raises(PlanDraftError, match="REQ-FRAME-001: MUST/MUST NOT"):
        _link(manifest=manifest)


def test_s5_gate_rejects_task_ready_contract() -> None:
    manifest = make_manifest()
    manifest["tests"][0]["required_contracts"] = ["server-process"]
    with pytest.raises(PlanDraftError, match="gate=s5"):
        _link(manifest=manifest)


def test_definition_requirement_may_have_no_primary_task() -> None:
    coverage = make_link_result().plan["coverage"]["requirements"]
    definition = next(row for row in coverage if row["req_id"] == "REQ-TRANSPORT-001")
    assert definition["primary_task_id"] is None
    assert definition["primary_work_package_id"] is None
    assert definition["test_nodeids"] == ["tests/l0_build/test_scaffold.py::test_builds"]


def test_definition_requirement_rejects_more_than_one_primary_task() -> None:
    tasks = deepcopy(make_link_result().plan["tasks"])
    for task in tasks:
        task["requirement_responsibilities"] = [
            *task["requirement_responsibilities"],
            {"req_id": "REQ-TRANSPORT-001", "role": "primary"},
        ]
    with pytest.raises(PlanDraftError, match="DEFINITION 不得有多个 primary"):
        _coverage(tasks, make_manifest())


# ---------------------------------------------------------------------------
# enabled 派生与构建变体
# ---------------------------------------------------------------------------


def test_enabled_set_follows_the_config_snapshot_l3_switch() -> None:
    """禁用只影响 enabled，静态 gate 映射保持不变（5.2.3）。"""
    manifest = make_manifest()
    l3 = "tests/l3_interop/test_reference.py::test_interop"
    assert l3 not in enabled_test_nodeids(manifest, make_config_snapshot())
    snapshot = make_config_snapshot()
    snapshot["stages"]["l3_interop"] = True
    assert l3 in enabled_test_nodeids(manifest, snapshot)
    rows = {
        row["nodeid"]: row
        for row in _link(config_snapshot=snapshot).plan["coverage"]["tests"]
    }
    assert rows[l3]["enabled"] is True
    assert rows[l3]["gate"] == "s7_only"
    assert rows[l3]["task_id"] is None


def test_build_variants_merge_required_variants_with_bound_test_variants() -> None:
    """required 变体是下界；绑定测试额外要求的变体并入同一集合（5.2.2）。"""
    constraints = make_constraints()
    for variant in constraints["build_variants"]:
        variant["required"] = variant["id"] == "release"
    tasks = {task["id"]: task for task in _link(constraints=constraints).plan["tasks"]}
    assert tasks["T-001"]["acceptance"] == {
        "build_variant_ids": ["release", "san"],
        "tests": ["tests/l1_codec/test_frame.py::test_roundtrip"],
    }
    assert tasks["T-003"]["acceptance"] == {
        "build_variant_ids": ["release"],
        "tests": [],
    }


def test_language_profile_without_required_variant_is_rejected() -> None:
    constraints = make_constraints()
    for variant in constraints["build_variants"]:
        variant["required"] = False
    with pytest.raises(PlanDraftError, match="required 构建变体"):
        _link(constraints=constraints)


def test_disabled_test_is_never_injected_into_task_acceptance() -> None:
    plan = make_link_result().plan
    injected = {
        nodeid for task in plan["tasks"] for nodeid in task["acceptance"]["tests"]
    }
    assert "tests/l3_interop/test_reference.py::test_interop" not in injected
    assert injected == {
        row["nodeid"]
        for row in plan["coverage"]["tests"]
        if row["gate"] == "task" and row["enabled"]
    }


# ---------------------------------------------------------------------------
# 步骤 8/9：Blueprint 与封口
# ---------------------------------------------------------------------------


def test_blueprint_seal_and_input_refs_are_injected_into_the_plan() -> None:
    result = make_link_result()
    blueprint = result.blueprint
    recomputed = {
        key: value for key, value in blueprint.items() if key != "content_sha256"
    }
    assert blueprint["content_sha256"] == canonical_sha256(recomputed)
    assert result.plan["delivery_blueprint_sha256"] == blueprint["content_sha256"]
    assert result.plan["input_refs"] == INPUT_REFS
    assert "delivery_blueprint_sha256" not in blueprint


def test_blueprint_owner_map_is_a_total_partition_of_task_files() -> None:
    result = make_link_result()
    owned = {
        item["path"]: item["owner_task_id"]
        for item in result.blueprint["files"]
        if item["owner_task_id"] is not None
    }
    declared = {
        path: task["id"]
        for task in result.plan["tasks"]
        for path in task["deliverable_files"]
    }
    assert owned == declared


def test_input_refs_are_deep_copied_from_the_caller() -> None:
    input_refs = deepcopy(INPUT_REFS)
    draft = make_draft()
    plan = link_plan_draft(
        draft,
        spec=make_spec(),
        manifest=make_manifest(),
        constraints=make_constraints(),
        input_refs=input_refs,
        config_snapshot=make_config_snapshot(),
    ).plan
    input_refs["spec"]["sha256"] = "00" * 32
    assert plan["input_refs"]["spec"]["sha256"] == INPUT_REFS["spec"]["sha256"]


def test_linker_never_reviews_and_leaves_a_pass_shell_by_default() -> None:
    assert make_link_result().plan["review"] == {
        "verdict": "pass",
        "unresolved_minor_issues": [],
    }


def test_controller_supplied_review_is_carried_into_the_plan() -> None:
    review = {
        "verdict": "pass_with_minor_issues",
        "unresolved_minor_issues": ["Transport error taxonomy stays coarse."],
    }
    assert _link(review=review).plan["review"] == review


def test_link_report_records_the_auditable_linking_decisions() -> None:
    report = make_link_result().link_report
    assert report["task_order"] == ["T-001", "T-002", "T-003", "T-004"]
    assert report["task_ids"] == {
        "wp-client/app": "T-002",
        "wp-codec/codec": "T-001",
        "wp-server/app": "T-004",
        "wp-transport/transport": "T-003",
    }
    assert report["task_edges"] == [
        {"task_id": "T-002", "depends_on": ["T-001"]},
        {"task_id": "T-004", "depends_on": ["T-001", "T-003"]},
    ]
    assert report["coverage_counts"] == {
        "requirements": len(make_spec()["requirements"]),
        "tests": len(make_manifest_tests()),
        "task_gated_tests": 3,
        "enabled_tests": 4,
    }


def test_linking_is_deterministic_for_shuffled_shard_and_task_order() -> None:
    """打乱 shard 顺序不改变任何最终 id、coverage 或封口哈希（6.4.5 步骤 4）。"""
    shards = list(reversed(make_shards()))
    assert _link(shards=shards).plan == make_link_result().plan
