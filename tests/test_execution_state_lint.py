"""execution_state_lint 的 git 支撑执行对账测试（设计 5.2.5、6.6）。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nepa.canonical import atomic_write_canonical_json, canonical_sha256
from nepa.plan_state import (
    AttemptStartedEvent,
    AttemptSucceededEvent,
    execution_state_lint,
    publish_initial_plan_state,
    transition_plan_state,
)
from nepa.speclib.lint import LintReport
from nepa.task_evidence import publish_task_evidence, task_evidence_relative_path
from nepa.tools.git_ops import GitOps
from tests.plan_v3 import example, make_config_snapshot, make_plan

TASK_ORDER = ("T-001", "T-002", "T-003", "T-004")


def _write_ref(run_dir: Path, relative: str, value: dict[str, Any] | str) -> dict[str, str]:
    """写入 receipt 目标文件；``str`` 用于构造内容合法但非 JSON 的来源文件。"""
    path = run_dir / relative
    if isinstance(value, str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    else:
        atomic_write_canonical_json(path, value)
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@dataclass(slots=True)
class Execution:
    """一次已执行到 S6 的 run：workspace 仓库、账本与阶段 receipts。"""

    run_dir: Path
    git: GitOps
    plan: dict[str, Any]
    config: dict[str, Any]
    seal: dict[str, Any]
    stage_receipts: dict[str, Any]

    @property
    def state_path(self) -> Path:
        return self.run_dir / "plan" / "plan_state.json"

    @property
    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def task(self, task_id: str) -> dict[str, Any]:
        return next(item for item in self.plan["tasks"] if item["id"] == task_id)

    def lint(
        self,
        *,
        state: dict[str, Any] | None = None,
        stage_receipts: dict[str, Any] | None = None,
        test_bundle: dict[str, Any] | None = None,
        require_clean: bool = True,
    ) -> LintReport:
        return execution_state_lint(
            self.plan,
            state if state is not None else self.state,
            self.git,
            self.run_dir,
            stage_receipts if stage_receipts is not None else self.stage_receipts,
            test_bundle=test_bundle,
            require_clean=require_clean,
        )

    def commit_task(
        self,
        task_id: str,
        *,
        with_summary: bool = False,
        summary: dict[str, Any] | str | None = None,
        evidence_sha256: str | None = None,
    ) -> str:
        """按 6.6 的顺序发布 attempt：写文件 → stage → 发布证据 → 带 trailer 提交。"""
        task = self.task(task_id)
        for relative in task["deliverable_files"]:
            path = self.git.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"/* {task_id} */\n", encoding="utf-8")
        transition_plan_state(
            self.state_path,
            task_id,
            AttemptStartedEvent(),
            plan=self.plan,
            s4_seal=self.seal,
            config_snapshot=self.config,
        )
        prepared = self.git.prepare_task_commit(task["deliverable_files"])
        build_refs = [
            _write_ref(
                self.run_dir,
                f"test_results/build/{task_id}-attempt-001-{variant}.json",
                {"variant": variant, "ok": True},
            )
            for variant in task["acceptance"]["build_variant_ids"]
        ]
        summary_refs = []
        if with_summary:
            summary_refs.append(
                _write_ref(
                    self.run_dir,
                    f"test_results/round_{task_id}/summary.json",
                    summary
                    if summary is not None
                    else {
                        "schema_version": "2.0",
                        "manifest_sha256": "f" * 64,
                        "bundle_tree_sha256": "e" * 64,
                    },
                )
            )
        evidence = publish_task_evidence(
            self.run_dir,
            task_id=task_id,
            attempt=1,
            plan_sha256=self.seal["plan"]["sha256"],
            workspace_tree=prepared.workspace_tree,
            build_result_refs=build_refs,
            test_summary_refs=summary_refs,
        )
        commit_sha = self.git.commit_prepared_task(
            prepared,
            task_id=task_id,
            title=task["title"],
            attempt=1,
            evidence_sha256=evidence_sha256 or evidence.ref["sha256"],
        )
        transition_plan_state(
            self.state_path,
            task_id,
            AttemptSucceededEvent(
                commit_sha=commit_sha,
                task_evidence_ref=evidence.ref,
            ),
            plan=self.plan,
            s4_seal=self.seal,
            config_snapshot=self.config,
        )
        return commit_sha


def _execute(
    tmp_path: Path,
    *,
    task_ids: tuple[str, ...] = TASK_ORDER,
    with_summary: bool = False,
) -> Execution:
    """构造一次 S5 已封口、给定任务已 done 的最小执行现场。"""
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    git = GitOps(workspace)
    first_commit = git.init_and_commit()

    plan = make_plan()
    config = make_config_snapshot()
    seal = {
        "plan": {"path": "plan/plan.json", "sha256": canonical_sha256(plan)},
        "config_snapshot_sha256": canonical_sha256(config),
        "delivery_blueprint_sha256": plan["delivery_blueprint_sha256"],
    }
    blueprint_sha256 = plan["delivery_blueprint_sha256"]
    stage_receipts = {
        "s4": {
            "plan": dict(seal["plan"]),
            "delivery_blueprint_sha256": blueprint_sha256,
        },
        "s5": {
            "artifact_manifest": _write_ref(
                run_dir,
                "plan/artifact_manifest.json",
                {
                    "schema_version": "1.0",
                    "delivery_blueprint_sha256": blueprint_sha256,
                },
            ),
            "contract_map": _write_ref(
                run_dir,
                "plan/contract_map.json",
                {
                    "schema_version": "1.0",
                    "delivery_blueprint_sha256": blueprint_sha256,
                },
            ),
            "workspace_head": first_commit,
        },
    }
    execution = Execution(
        run_dir=run_dir,
        git=git,
        plan=plan,
        config=config,
        seal=seal,
        stage_receipts=stage_receipts,
    )
    publish_initial_plan_state(execution.state_path, plan, seal, config)
    for task_id in task_ids:
        execution.commit_task(task_id, with_summary=with_summary)
    return execution


def _codes(report: LintReport) -> set[str]:
    return set(report.error_codes())


def _git(git: GitOps, *args: str) -> None:
    """测试专用：直接驱动 workspace 仓库构造对账现场（生产代码不需要这些操作）。"""
    subprocess.run(["git", *args], cwd=git.workspace, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# 正向：完整执行现场
# ---------------------------------------------------------------------------


def test_fully_executed_run_passes_execution_state_lint(tmp_path: Path) -> None:
    """S5 锚点、trailer、证据与依赖祖先全部一致 → 0 error（5.2.5）。"""
    execution = _execute(tmp_path)
    report = execution.lint()
    assert report.errors == []
    assert [task["status"] for task in execution.state["tasks"]] == ["done"] * 4


def test_partially_executed_run_passes_execution_state_lint(tmp_path: Path) -> None:
    """未开始的 task 不参与 commit 对账，pending 现场同样合法。"""
    execution = _execute(tmp_path, task_ids=("T-001", "T-003"))
    assert execution.lint().errors == []


def test_test_summary_bound_to_the_frozen_bundle_passes(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",), with_summary=True)
    report = execution.lint(test_bundle=example("test-bundle.json"))
    assert report.errors == []


# ---------------------------------------------------------------------------
# S4/S5 锚点
# ---------------------------------------------------------------------------


def test_non_v3_plan_stops_execution_reconciliation(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    execution.plan["schema_version"] = "2.0"
    assert _codes(execution.lint()) == {"EXEC-PLAN-VERSION"}


def test_s4_plan_receipt_drift_reports_plan_seal(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    receipts = deepcopy(execution.stage_receipts)
    receipts["s4"]["plan"]["sha256"] = "0" * 64
    codes = _codes(execution.lint(stage_receipts=receipts))
    assert "EXEC-PLAN-SEAL" in codes
    assert "EXEC-PLAN-REF" in codes


def test_blueprint_seal_drift_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    receipts = deepcopy(execution.stage_receipts)
    receipts["s4"]["delivery_blueprint_sha256"] = "0" * 64
    assert "EXEC-BLUEPRINT-SEAL" in _codes(execution.lint(stage_receipts=receipts))


def test_missing_s5_output_refs_are_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    receipts = deepcopy(execution.stage_receipts)
    del receipts["s5"]
    assert "EXEC-STAGE-RECEIPT" in _codes(execution.lint(stage_receipts=receipts))


def test_missing_s5_artifact_file_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    (execution.run_dir / "plan" / "contract_map.json").unlink()
    assert "EXEC-ARTIFACT-MISSING" in _codes(execution.lint())


def test_tampered_s5_artifact_content_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    path = execution.run_dir / "plan" / "artifact_manifest.json"
    path.write_text("{}\n", encoding="utf-8")
    assert "EXEC-ARTIFACT-HASH" in _codes(execution.lint())


def test_s5_artifact_with_stale_blueprint_hash_reports_drift(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    receipts = deepcopy(execution.stage_receipts)
    receipts["s5"]["contract_map"] = _write_ref(
        execution.run_dir,
        "plan/contract_map.json",
        {"schema_version": "1.0", "delivery_blueprint_sha256": "0" * 64},
    )
    assert "EXEC-BLUEPRINT-DRIFT" in _codes(execution.lint(stage_receipts=receipts))


def test_s5_head_that_is_not_an_ancestor_reports_git_ancestry(tmp_path: Path) -> None:
    """S5 首提交必须仍是当前 HEAD 的祖先，否则历史已被改写（6.6）。"""
    execution = _execute(tmp_path, task_ids=("T-001",))
    receipts = deepcopy(execution.stage_receipts)
    receipts["s5"]["workspace_head"] = "0" * 40
    assert "EXEC-GIT-ANCESTRY" in _codes(execution.lint(stage_receipts=receipts))


def test_task_commit_outside_the_current_history_reports_git_ancestry(
    tmp_path: Path,
) -> None:
    """state 指向的 commit 存在于对象库但不在 HEAD 祖先链上（分支被丢弃）。"""
    execution = _execute(tmp_path, task_ids=("T-001",))
    head = execution.git.head()
    # 重写同一 tree/trailers 到另一条分支：证据全部通过，只有祖先关系被破坏。
    _git(execution.git, "checkout", "-q", "-b", "abandoned")
    _git(execution.git, "commit", "-q", "--amend", "--no-edit", "--date=2000-01-01T00:00:00")
    abandoned = execution.git.head()
    _git(execution.git, "checkout", "-q", head)
    assert abandoned != head
    state = execution.state
    state["tasks"][0]["commit_sha"] = abandoned
    assert _codes(execution.lint(state=state)) == {"EXEC-GIT-ANCESTRY"}


# ---------------------------------------------------------------------------
# task commit、trailer 与证据
# ---------------------------------------------------------------------------


def test_done_task_without_commit_sha_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    state = execution.state
    state["tasks"][0].update({"status": "done", "attempts": 1})
    assert "EXEC-DONE-INCOMPLETE" in _codes(execution.lint(state=state))


def test_commit_without_task_trailers_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=())
    state = execution.state
    state["tasks"][0].update(
        {
            "status": "done",
            "attempts": 1,
            "commit_sha": execution.stage_receipts["s5"]["workspace_head"],
        }
    )
    assert "EXEC-COMMIT-TRAILER" in _codes(execution.lint(state=state))


def test_trailer_attempt_mismatch_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",))
    state = execution.state
    state["tasks"][0]["attempts"] = 2
    assert "EXEC-COMMIT-TRAILER" in _codes(execution.lint(state=state))


def test_done_task_without_evidence_ref_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",))
    state = execution.state
    state["tasks"][0]["acceptance_evidence"] = {"task_evidence_ref": None}
    assert "EXEC-EVIDENCE-MISSING" in _codes(execution.lint(state=state))


def test_tampered_evidence_content_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",))
    path = execution.run_dir / task_evidence_relative_path("T-001", 1)
    path.write_text("{}\n", encoding="utf-8")
    codes = _codes(execution.lint())
    assert "EXEC-EVIDENCE-INVALID" in codes


def test_evidence_sha256_not_matching_the_commit_trailer_is_reported(
    tmp_path: Path,
) -> None:
    """先按错误 trailer 提交，再让 state 指向真实证据（trailer 与内容不一致）。"""
    execution = _execute(tmp_path, task_ids=())
    execution.commit_task("T-001", evidence_sha256="0" * 64)
    codes = _codes(execution.lint())
    assert "EXEC-EVIDENCE-TRAILER" in codes


def test_orphan_evidence_without_any_done_commit_is_reported(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",))
    _write_ref(
        execution.run_dir,
        "test_results/build/T-002-attempt-001.json",
        {"variant": "release", "ok": True},
    )
    orphan = execution.run_dir / task_evidence_relative_path("T-002", 1)
    atomic_write_canonical_json(
        orphan,
        {
            "schema_version": "1.0",
            "task_id": "T-002",
            "attempt": 1,
            "plan_sha256": execution.seal["plan"]["sha256"],
            "workspace_tree": execution.git.commit_tree(),
            "build_result_refs": [],
            "test_summary_refs": [],
        },
    )
    codes = _codes(execution.lint())
    assert "EXEC-EVIDENCE-ORPHAN" in codes


def test_done_task_whose_dependency_is_not_done_reports_dependency_order(
    tmp_path: Path,
) -> None:
    """T-002 依赖 T-001；仅 T-002 done 的账本缺少可对账的依赖 commit。"""
    execution = _execute(tmp_path, task_ids=("T-001", "T-002"))
    state = execution.state
    for task in state["tasks"]:
        if task["id"] == "T-001":
            task.update(
                {
                    "status": "pending",
                    "attempts": 0,
                    "commit_sha": None,
                    "acceptance_evidence": {"task_evidence_ref": None},
                }
            )
    codes = _codes(execution.lint(state=state))
    assert "EXEC-DEPENDENCY-ORDER" in codes


def test_dirty_workspace_is_reported_and_can_be_waived(tmp_path: Path) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",))
    (execution.git.workspace / "src" / "codec" / "codec_connect.c").write_text(
        "/* uncommitted */\n", encoding="utf-8"
    )
    assert "EXEC-WORKSPACE-DIRTY" in _codes(execution.lint())
    assert execution.lint(require_clean=False).errors == []


# ---------------------------------------------------------------------------
# Test Bundle 双摘要
# ---------------------------------------------------------------------------


def test_summary_bound_to_a_different_bundle_reports_bundle_drift(
    tmp_path: Path,
) -> None:
    execution = _execute(tmp_path, task_ids=("T-001",), with_summary=True)
    bundle = example("test-bundle.json")
    bundle["bundle_tree_sha256"] = "1" * 64
    assert "EXEC-BUNDLE-DRIFT" in _codes(
        execution.lint(test_bundle=bundle)
    )


def test_unreadable_test_summary_reports_summary_invalid(tmp_path: Path) -> None:
    """摘要内容哈希与证据一致但不是合法 JSON → 只报 SUMMARY-INVALID。"""
    execution = _execute(tmp_path, task_ids=())
    execution.commit_task("T-001", with_summary=True, summary="not json\n")
    codes = _codes(execution.lint(test_bundle=example("test-bundle.json")))
    assert "EXEC-SUMMARY-INVALID" in codes
    assert "EXEC-EVIDENCE-INVALID" not in codes

