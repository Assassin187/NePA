"""S4 Plan Compiler 控制器测试：状态机、检查点、预算与封口（6.4.2～6.4.7）。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nepa.canonical import canonical_sha256
from nepa.run_store import RunStore
from nepa.stages.s4_plan import (
    S4Error,
    _strip_redundant_cross_package_dependencies,
    compile_plan,
)
from tests.plan_v3 import make_manifest, make_spec
from tests.s4_stubs import (
    FAIL,
    Harness,
    Truncated,
    architecture_draft,
    build_harness,
    critic,
    flat_draft,
    flat_queues,
    issue,
    layered_queues,
    make_config,
    ordered_shards,
    task_shards,
)


def _run(harness: Harness, **kwargs: Any) -> Any:
    return compile_plan(
        harness.store,
        harness.config,
        harness.inputs,
        harness.runner,
        harness.budget,
        **kwargs,
    )


def test_cross_package_ids_are_mechanically_removed_from_local_dependencies() -> None:
    shard = {
        "tasks": [
            {
                "local_id": "implement",
                "depends_on": ["upstream-package", "local-helper", "unknown"],
            },
            {"local_id": "local-helper", "depends_on": []},
        ]
    }
    architecture = {
        "work_packages": [
            {"id": "current-package"},
            {"id": "upstream-package"},
            # A local id wins if it happens to equal a package id.
            {"id": "local-helper"},
        ]
    }

    normalized = _strip_redundant_cross_package_dependencies(
        shard,
        architecture=architecture,
    )

    assert normalized["tasks"][0]["depends_on"] == ["local-helper", "unknown"]
    assert shard["tasks"][0]["depends_on"] == [
        "upstream-package",
        "local-helper",
        "unknown",
    ]


# ---- 正常路径与封口 -----------------------------------------------------


def test_layered_happy_path_publishes_and_writes_seal_receipt(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    result = _run(harness)

    assert result.published is True
    assert result.plan_path == harness.run_dir / "plan" / "plan.json"
    assert harness.role_calls("architecture_planner") == 1
    assert harness.role_calls("task_planner") == 4  # 每个工作包串行一次（4.9）
    assert harness.role_calls("plan_critic") == 1

    published = json.loads(result.plan_path.read_text(encoding="utf-8"))
    stage = harness.store.meta.stages["s4"]
    assert stage.status == "done"
    assert stage.output_refs is not None
    assert stage.output_refs["plan"] == {
        "path": "plan/plan.json",
        "sha256": canonical_sha256(published),
    }
    assert stage.output_refs["delivery_blueprint_sha256"] == published[
        "delivery_blueprint_sha256"
    ]
    assert stage.output_refs["config_snapshot_sha256"] == canonical_sha256(
        harness.store.meta.config_snapshot
    )
    assert published["input_refs"] == dict(harness.inputs.input_refs)
    assert harness.state()["status"] == "sealed"
    assert harness.state()["phase"] == "SEAL_AND_PUBLISH"


def test_checkpoints_are_written_with_parent_hash_chain(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    _run(harness)

    checkpoints = harness.state()["checkpoints"]
    expected = {
        "delivery_constraints.json",
        "planning_index.json",
        "architecture_candidate.json",
        "link_report.json",
        "delivery_blueprint.json",
        "candidate_plan.json",
        "reviews/round_001.json",
    } | {f"task_shards/{key}.json" for key in task_shards()}
    assert expected <= set(checkpoints)
    for relative, entry in checkpoints.items():
        assert entry["valid"] is True
        path = harness.s4_dir / relative
        assert canonical_sha256(json.loads(path.read_text(encoding="utf-8"))) == entry["sha256"]

    # planning_index 的父锚是 delivery_constraints 的内容哈希（5.6.6）。
    constraints = json.loads(
        (harness.s4_dir / "delivery_constraints.json").read_text(encoding="utf-8")
    )
    assert checkpoints["planning_index.json"]["parent_sha256"] == constraints["content_sha256"]
    architecture_parent = checkpoints["architecture_candidate.json"]["sha256"]
    for key in task_shards():
        entry = checkpoints[f"task_shards/{key}.json"]
        assert entry["parent_sha256"] != architecture_parent  # shard 锚含 payload
        assert entry["valid"] is True


def test_review_round_records_controller_verdict_next_to_critic_verdict(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    _run(harness)

    review = json.loads(
        (harness.s4_dir / "reviews" / "round_001.json").read_text(encoding="utf-8")
    )
    assert review["round"] == 1
    assert review["controller_verdict"] == "pass"
    assert review["critic_verdict"] == "pass"
    assert review["lint_error_codes"] == []
    assert review["issues"] == []


def test_plan_critic_payload_hides_test_implementation_details(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    _run(harness)

    prompt = harness.prompts("plan_critic")[0]
    # 模板自己会声明 oracle/runner 不可见，这里只查 payload 是否泄漏具体引用。
    assert "harness/oracle.py" not in prompt
    assert "harness/target.py" not in prompt
    assert "command_prefix" not in prompt
    assert "pytest-runner" not in prompt


def test_every_s4_role_prompt_renders_its_payload(tmp_path: Path) -> None:
    """模板漏掉 payload_json 会让角色收到空输入，必须在门里挡住。"""
    harness = build_harness(tmp_path, layered_queues())
    _run(harness)

    assert "planning_index" in harness.prompts("architecture_planner")[0]
    assert "allowed_files" in harness.prompts("task_planner")[0]
    assert "lint_report" in harness.prompts("plan_critic")[0]

    flat = build_harness(tmp_path / "flat", flat_queues(), strategy="flat")
    _run(flat)
    assert "delivery_constraints" in flat.prompts("flat_plan_baseline")[0]


def test_task_planner_payload_only_exposes_manifest_metadata(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    _run(harness)

    for prompt in harness.prompts("task_planner"):
        assert "pytest-runner" not in prompt
        assert "harness/oracle.py" not in prompt
        assert '"layer"' not in prompt


# ---- verdict 复核与 minor 归一化 ----------------------------------------


def test_controller_overrides_a_pass_verdict_that_still_carries_a_major(tmp_path: Path) -> None:
    """6.4.6：控制器自行复核 verdict，pass + major 必须转 revise。"""
    harness = build_harness(
        tmp_path,
        layered_queues(
            critics=[
                critic("pass", [issue(severity="major", scope="architecture", target_id="core")]),
                critic("pass"),
            ],
            architecture=[architecture_draft(), architecture_draft()],
            shards=ordered_shards() + ordered_shards(),
        ),
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("plan_critic") == 2
    assert harness.role_calls("architecture_planner") == 2  # 全局问题回架构一次
    first = json.loads((harness.s4_dir / "reviews" / "round_001.json").read_text("utf-8"))
    assert (first["critic_verdict"], first["controller_verdict"]) == ("pass", "revise")


def test_unresolved_minor_issues_are_renumbered_into_the_published_plan(tmp_path: Path) -> None:
    harness = build_harness(
        tmp_path,
        layered_queues(
            critics=[
                critic(
                    "pass",
                    [
                        issue(
                            issue_id="PI-042",
                            severity="minor",
                            scope="task",
                            target_id="T-002",
                            code="CLARIFY_RETURN",
                        ),
                        issue(
                            issue_id="PI-007",
                            severity="minor",
                            scope="architecture",
                            target_id="core",
                            code="CLARIFY_BOUNDARY",
                        ),
                    ],
                )
            ]
        ),
    )
    result = _run(harness)

    review = result.plan["review"]
    assert review["verdict"] == "pass"
    assert [item["id"] for item in review["unresolved_minor_issues"]] == ["PI-001", "PI-002"]
    assert [item["code"] for item in review["unresolved_minor_issues"]] == [
        "CLARIFY_BOUNDARY",
        "CLARIFY_RETURN",
    ]


# ---- 定点修复路由 -------------------------------------------------------


def test_local_critic_issue_only_reexpands_the_owning_work_package(tmp_path: Path) -> None:
    shards = task_shards()
    harness = build_harness(
        tmp_path,
        layered_queues(
            critics=[
                critic("revise", [issue(scope="work_package", target_id="wp-client")]),
                critic("pass"),
            ],
            shards=ordered_shards() + [shards["wp-client"]],
        ),
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 1
    assert harness.role_calls("task_planner") == 5  # 只重做 wp-client
    state = harness.state()
    assert state["repairs"]["work_packages"] == {"wp-client": 1}
    assert state["repairs"]["plan_critic"] == 1
    assert state["repairs"]["plan_global_replans"] == 0
    assert state["attempts"]["work_packages"]["wp-client"] == 2
    repair_prompt = harness.prompts("task_planner")[-1]
    assert "semantic_validation_errors" in repair_prompt
    assert "wp-client" in repair_prompt


def test_shard_gate_failure_consumes_one_local_redo(tmp_path: Path) -> None:
    shards = task_shards()
    broken = deepcopy(shards["wp-codec"])
    broken["tasks"][0]["deliverable_files"] = ["src/codec/not_allowed.c"]
    queue = ordered_shards()
    # 展开顺序按 id 升序，wp-codec 的首次回答换成越界文件的版本。
    queue.insert(queue.index(shards["wp-codec"]), broken)
    harness = build_harness(tmp_path, layered_queues(shards=queue))
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("task_planner") == 5
    state = harness.state()
    assert state["repairs"]["work_packages"] == {"wp-codec": 1}
    assert state["repairs"]["plan_critic"] == 0  # shard 门失败不消耗 critic 预算


def test_second_shard_gate_failure_exhausts_the_package_budget(tmp_path: Path) -> None:
    shards = task_shards()
    first = min(shards)
    broken = deepcopy(shards[first])
    broken["tasks"][0]["deliverable_files"] = ["src/codec/not_allowed.c"]
    harness = build_harness(
        tmp_path,
        layered_queues(shards=[deepcopy(broken), deepcopy(broken)]),
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_SHARD_BUDGET_EXHAUSTED"
    assert harness.role_calls("task_planner") == 2  # 首个包用尽预算即停
    state = harness.state()
    assert state["repairs"]["work_packages"] == {first: 1}
    assert state["status"] == "failed"
    assert state["failure"]["code"] == "PLAN_SHARD_BUDGET_EXHAUSTED"
    assert not (harness.run_dir / "plan" / "plan.json").exists()  # 不发布部分 Plan
    assert harness.store.meta.stages["s4"].status == "running"


def test_global_critic_issue_replans_architecture_and_reexpands_every_shard(
    tmp_path: Path,
) -> None:
    harness = build_harness(
        tmp_path,
        layered_queues(
            architecture=[architecture_draft(), architecture_draft()],
            shards=ordered_shards() + ordered_shards(),
            critics=[
                critic("revise", [issue(scope="architecture", target_id="core")]),
                critic("pass"),
            ],
        ),
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 2
    assert harness.role_calls("task_planner") == 8
    state = harness.state()
    assert state["repairs"]["plan_global_replans"] == 1
    assert state["repairs"]["plan_critic"] == 1
    assert state["attempts"]["architecture"] == 2
    replan_prompt = harness.prompts("architecture_planner")[-1]
    assert "previous_candidate" in replan_prompt
    assert "semantic_validation_errors" in replan_prompt


def test_second_global_problem_exhausts_the_global_replan_budget(tmp_path: Path) -> None:
    harness = build_harness(
        tmp_path,
        layered_queues(
            architecture=[architecture_draft(), architecture_draft()],
            shards=ordered_shards() + ordered_shards(),
            critics=[
                critic("revise", [issue(scope="architecture", target_id="core", code="A_ONE")]),
                critic("revise", [issue(scope="architecture", target_id="core", code="A_TWO")]),
            ],
        ),
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_GLOBAL_REPLAN_EXHAUSTED"
    assert harness.state()["failure"]["code"] == "PLAN_GLOBAL_REPLAN_EXHAUSTED"


def test_critic_budget_exhaustion_stops_before_a_third_semantic_round(tmp_path: Path) -> None:
    config = make_config()
    config.budgets.plan_critic_repairs = 0
    harness = build_harness(tmp_path, layered_queues(critics=[critic("revise", [issue()])]),
                            config=config)
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_CRITIC_BUDGET_EXHAUSTED"


def test_repeated_issue_signature_is_reported_as_non_convergence(tmp_path: Path) -> None:
    same = issue(scope="work_package", target_id="wp-client", code="MISSING_ERROR_PATH")
    shards = task_shards()
    config = make_config()
    config.budgets.plan_critic_repairs = 3
    harness = build_harness(
        tmp_path,
        layered_queues(
            shards=ordered_shards() + [shards["wp-client"]],
            critics=[critic("revise", [deepcopy(same)]), critic("revise", [deepcopy(same)])],
        ),
        config=config,
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_NOT_CONVERGING"
    state = harness.state()
    assert state["seen_issue_signatures"] == [
        "blocker|work_package|wp-client|MISSING_ERROR_PATH"
    ]


# ---- 生产门与结构化输出 -------------------------------------------------


def test_arch_validate_failure_allows_exactly_one_pointed_repair(tmp_path: Path) -> None:
    broken = architecture_draft()
    broken["architecture"]["modules"][0]["responsibilities"] = []
    harness = build_harness(
        tmp_path,
        layered_queues(architecture=[broken, architecture_draft()]),
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 2
    state = harness.state()
    assert state["repairs"]["architecture"] == 1
    assert state["attempts"]["architecture"] == 2


def test_second_arch_validate_failure_fails_the_stage(tmp_path: Path) -> None:
    broken = architecture_draft()
    broken["architecture"]["modules"][0]["responsibilities"] = []
    harness = build_harness(
        tmp_path,
        layered_queues(architecture=[deepcopy(broken), deepcopy(broken)]),
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_ARCH_VALIDATE_FAILED"
    assert harness.state()["repairs"]["architecture"] == 1


def test_structured_output_failure_fails_without_publishing(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues(architecture=[FAIL]))
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_STRUCTURED_OUTPUT_FAILED"
    assert harness.state()["status"] == "failed"
    assert not (harness.run_dir / "plan" / "plan.json").exists()


def test_truncated_output_fails_the_stage_without_publishing(tmp_path: Path) -> None:
    """6.4.6：模型输出截断使 S4 failed，不得当作正常候选继续。"""
    harness = build_harness(
        tmp_path,
        layered_queues(architecture=[Truncated(architecture_draft())]),
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_OUTPUT_TRUNCATED"
    assert harness.state()["failure"]["code"] == "PLAN_OUTPUT_TRUNCATED"
    assert not (harness.run_dir / "plan" / "plan.json").exists()


def test_every_s4_call_carries_its_compiler_phase_evidence(tmp_path: Path) -> None:
    """5.5：S4 调用额外记录编译阶段、工作包、父工件哈希与修复预算。"""
    shards = task_shards()
    harness = build_harness(
        tmp_path,
        layered_queues(
            critics=[
                critic("revise", [issue(scope="work_package", target_id="wp-client")]),
                critic("pass"),
            ],
            shards=ordered_shards() + [shards["wp-client"]],
        ),
    )
    _run(harness)

    extras = harness.trace_extras()
    assert len(extras) == harness.role_calls("architecture_planner") + harness.role_calls(
        "task_planner"
    ) + harness.role_calls("plan_critic")
    for entry in extras:
        assert entry["compiler_phase"]
        assert len(entry["parent_artifact_sha256"]) == 64
        assert set(entry["repair_budget_used"]) == {
            "architecture",
            "work_package",
            "plan_critic",
            "plan_global_replans",
        }
    phases = [entry["compiler_phase"] for entry in extras]
    assert phases[0] == "ARCHITECT"
    assert "REEXPAND_WORK_PACKAGE" in phases
    assert "PLAN_CRITIC" in phases

    reexpand = next(entry for entry in extras if entry["compiler_phase"] == "REEXPAND_WORK_PACKAGE")
    assert reexpand["work_package_id"] == "wp-client"
    assert reexpand["repair_budget_used"]["work_package"] == 1
    assert reexpand["repair_budget_used"]["plan_critic"] == 1
    # 架构调用不属于任何工作包，字段显式记 null 而不是省略。
    assert extras[0]["work_package_id"] is None


def test_mechanical_issue_only_relinks_without_spending_semantic_budget(
    tmp_path: Path,
) -> None:
    """6.4.6：机械问题由控制器修正后重新链接，不回架构也不动 shard。"""
    harness = build_harness(
        tmp_path,
        layered_queues(
            critics=[
                critic("revise", [issue(scope="mechanical", target_id="coverage")]),
                critic("pass"),
            ]
        ),
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 1
    assert harness.role_calls("task_planner") == 4
    assert harness.role_calls("plan_critic") == 2
    state = harness.state()
    assert state["repairs"] == {
        "architecture": 0,
        "work_packages": {},
        "plan_critic": 0,
        "plan_global_replans": 0,
    }


def test_repeated_mechanical_issue_is_reported_as_non_convergence(tmp_path: Path) -> None:
    """重链是幂等的，同一机械 signature 复现只能是不收敛。"""
    same = issue(scope="mechanical", target_id="coverage", code="STALE_INDEX")
    harness = build_harness(
        tmp_path,
        layered_queues(
            critics=[critic("revise", [deepcopy(same)]), critic("revise", [deepcopy(same)])]
        ),
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_NOT_CONVERGING"


def test_config_snapshot_drift_is_rejected_at_prepare(tmp_path: Path) -> None:
    """6.4.3 步骤 1：Run v2 配置快照哈希必须与快照内容一致。

    盘上漂移已由 `RunStore.load` 的 RunMeta 校验拦住；这里覆盖控制器自己的
    复核，即进程内被改过的 store 也不能进入规划。
    """
    harness = build_harness(tmp_path, layered_queues())
    run_json = harness.run_dir / "run.json"
    meta = json.loads(run_json.read_text(encoding="utf-8"))
    meta["config_snapshot"]["planning"]["max_task_files"] = 3
    run_json.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="config_snapshot_sha256"):
        RunStore.load(harness.run_dir)

    harness.store.meta.config_snapshot["planning"]["max_task_files"] = 3
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_INPUTS_INVALID"
    assert harness.client.calls == []


def test_input_ref_disagreeing_with_run_json_is_rejected(tmp_path: Path) -> None:
    """冻结输入的引用必须与 run.json inputs 声明逐字段一致。"""
    harness = build_harness(tmp_path, layered_queues())
    inputs = replace(
        harness.inputs,
        input_refs={
            **harness.inputs.input_refs,
            "spec": {"path": "spec/spec.json", "sha256": "ab" * 32},
        },
    )
    with pytest.raises(S4Error) as excinfo:
        compile_plan(
            harness.store, harness.config, inputs, harness.runner, harness.budget
        )

    assert excinfo.value.code == "PLAN_INPUTS_INVALID"
    assert harness.client.calls == []


def test_spec_lint_failure_stops_before_any_llm_call(tmp_path: Path) -> None:
    spec = make_spec()
    spec["requirements"] = [
        item for item in spec["requirements"] if item["id"] != "REQ-CONNECT-001"
    ]
    harness = build_harness(tmp_path, layered_queues(), spec=spec)
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_SPEC_LINT_FAILED"
    assert harness.client.calls == []


def test_drifted_frozen_input_is_rejected_at_prepare(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    spec_path = harness.run_dir / "spec" / "spec.json"
    spec_path.write_text(spec_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_INPUTS_INVALID"
    assert harness.client.calls == []


def test_in_memory_input_must_match_its_frozen_run_file(tmp_path: Path) -> None:
    """调用方不能拿一份已冻结的引用替换为另一份内存 Spec。"""
    harness = build_harness(tmp_path, layered_queues())
    altered = deepcopy(harness.inputs.spec)
    altered["requirements"][1]["text"] = "A different requirement text."
    inputs = replace(harness.inputs, spec=altered)

    with pytest.raises(S4Error) as excinfo:
        compile_plan(
            harness.store, harness.config, inputs, harness.runner, harness.budget
        )

    assert excinfo.value.code == "PLAN_INPUTS_INVALID"
    assert harness.client.calls == []


def test_unknown_strategy_is_rejected(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    with pytest.raises(S4Error) as excinfo:
        _run(harness, strategy="hybrid")

    assert excinfo.value.code == "PLAN_STRATEGY_INVALID"


# ---- flat（A9 消融） ----------------------------------------------------


def test_flat_strategy_uses_one_call_and_never_touches_layered_roles(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, flat_queues(), strategy="flat")
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("flat_plan_baseline") == 1
    assert harness.role_calls("architecture_planner") == 0
    assert harness.role_calls("task_planner") == 0
    state = harness.state()
    assert state["strategy"] == "flat"
    assert "flat_draft.json" in state["checkpoints"]


def test_flat_revise_redraws_the_whole_draft_and_spends_both_budgets(tmp_path: Path) -> None:
    harness = build_harness(
        tmp_path,
        flat_queues(
            drafts=[flat_draft(), flat_draft()],
            critics=[
                critic("revise", [issue(scope="work_package", target_id="wp-client")]),
                critic("pass"),
            ],
        ),
        strategy="flat",
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("flat_plan_baseline") == 2
    state = harness.state()
    assert state["repairs"]["plan_critic"] == 1
    assert state["repairs"]["plan_global_replans"] == 1
    assert state["repairs"]["work_packages"] == {}  # flat 不用分层配额


def test_flat_never_falls_back_to_layered_on_gate_failure(tmp_path: Path) -> None:
    broken = flat_draft()
    broken["tasks"][0]["deliverable_files"] = ["src/codec/not_allowed.c"]
    harness = build_harness(
        tmp_path,
        flat_queues(drafts=[deepcopy(broken), deepcopy(broken)]),
        strategy="flat",
    )
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_FLAT_VALIDATE_FAILED"
    assert harness.role_calls("architecture_planner") == 0


# ---- resume 与只读 no-op -----------------------------------------------


def test_resume_reuses_checkpoints_and_makes_no_new_llm_call(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues(critics=[FAIL]))
    with pytest.raises(S4Error):
        _run(harness)
    assert harness.role_calls("architecture_planner") == 1
    assert harness.role_calls("task_planner") == 4

    harness.enqueue({"plan_critic": [critic()]})
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 1  # 架构候选按父哈希复用
    assert harness.role_calls("task_planner") == 4
    assert harness.store.meta.stages["s4"].status == "done"


def test_resume_discards_checkpoints_when_the_planning_fingerprint_drifts(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path, layered_queues(critics=[FAIL]))
    with pytest.raises(S4Error):
        _run(harness)

    harness.config.planning.max_task_files = 3
    harness.enqueue(
        {
            "architecture_planner": [architecture_draft()],
            "task_planner": ordered_shards(),
            "plan_critic": [critic()],
        }
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 2  # 指纹漂移作废旧候选
    assert harness.state()["attempts"]["architecture"] == 1


def test_tampered_checkpoint_content_is_not_reused(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues(critics=[FAIL]))
    with pytest.raises(S4Error):
        _run(harness)

    path = harness.s4_dir / "architecture_candidate.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["architecture"]["assumptions"] = ["tampered"]
    path.write_text(json.dumps(tampered), encoding="utf-8")
    harness.enqueue(
        {
            "architecture_planner": [architecture_draft()],
            "task_planner": ordered_shards(),
            "plan_critic": [critic()],
        }
    )
    result = _run(harness)

    assert result.published is True
    assert harness.role_calls("architecture_planner") == 2


def test_byte_identical_published_plan_is_reused_when_the_receipt_is_missing(
    tmp_path: Path,
) -> None:
    """6.4.7：正式 Plan 已在盘上但 S4 未 done 时，逐字节一致才可直接补 receipt。"""
    harness = build_harness(tmp_path, layered_queues())
    result = _run(harness)
    published = result.plan_path.read_bytes()

    # 模拟"发布后、写 receipt 前"崩溃：清掉 receipt，保留正式 Plan 与检查点。
    harness.store.meta.stages["s4"].status = "running"
    harness.store.meta.stages["s4"].output_refs = None
    harness.store.save()
    state_path = harness.s4_dir / "s4_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running"
    state.pop("seal", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    harness.enqueue({"plan_critic": [critic()]})

    again = _run(harness)

    assert again.published is True
    assert again.plan_path.read_bytes() == published  # 未重写正式 Plan
    assert harness.state()["seal"]["reused_existing_plan"] is True
    assert harness.role_calls("architecture_planner") == 1  # 语义候选按父哈希复用
    assert harness.store.meta.stages["s4"].status == "done"


def test_divergent_leftover_plan_is_republished_from_the_verified_candidate(
    tmp_path: Path,
) -> None:
    """崩溃残留的正式 Plan 与候选不一致时不可信，必须重新发布。"""
    harness = build_harness(tmp_path, layered_queues())
    result = _run(harness)
    expected = result.plan_path.read_bytes()

    harness.store.meta.stages["s4"].status = "running"
    harness.store.meta.stages["s4"].output_refs = None
    harness.store.save()
    state_path = harness.s4_dir / "s4_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running"
    state.pop("seal", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result.plan_path.write_text('{"schema_version":"3.0"}', encoding="utf-8")
    harness.enqueue({"plan_critic": [critic()]})

    again = _run(harness)

    assert again.published is True
    assert again.plan_path.read_bytes() == expected
    assert harness.state()["seal"]["reused_existing_plan"] is False


def test_s4_done_is_a_read_only_no_op(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    first = _run(harness)
    published = first.plan_path.read_bytes()

    again = _run(harness)

    assert again.published is False
    assert again.plan == first.plan
    assert first.plan_path.read_bytes() == published
    assert harness.client.queues["plan_critic"] == []  # 没有新的 LLM 调用


def test_seal_receipt_mismatch_is_reported_on_the_next_entry(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, layered_queues())
    result = _run(harness)
    tampered = json.loads(result.plan_path.read_text(encoding="utf-8"))
    tampered["architecture"]["assumptions"] = ["tampered after seal"]
    result.plan_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_RECEIPT_INVALID"


def test_manifest_schema_version_drift_from_the_bundle_is_rejected(tmp_path: Path) -> None:
    manifest = make_manifest()
    manifest["schema_version"] = "1.0"
    harness = build_harness(tmp_path, layered_queues(), manifest=manifest)
    with pytest.raises(S4Error) as excinfo:
        _run(harness)

    assert excinfo.value.code == "PLAN_INPUTS_INVALID"
