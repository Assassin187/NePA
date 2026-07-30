"""Plan State v1 初始化与 snapshot lint（设计 5.2.4～5.2.5）。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json, canonical_sha256
from nepa.speclib.lint import LintIssue, LintReport
from nepa.task_evidence import TaskEvidenceValidationError, validate_task_evidence_ref
from nepa.tools.git_ops import GitError, GitOps

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "plan-state.schema.json"


class PlanStateValidationError(ValueError):
    """Plan State 初始化输入或 snapshot 不满足冻结不变量。"""


@dataclass(frozen=True, slots=True)
class AttemptStartedEvent:
    kind: Literal["attempt_started"] = "attempt_started"
    previous_error: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptSucceededEvent:
    commit_sha: str
    task_evidence_ref: dict[str, str]
    notes: str = ""
    kind: Literal["attempt_succeeded"] = "attempt_succeeded"


@dataclass(frozen=True, slots=True)
class AttemptsExhaustedEvent:
    detail: str
    notes: str = ""
    kind: Literal["attempts_exhausted"] = "attempts_exhausted"


@dataclass(frozen=True, slots=True)
class DependencyBlockedEvent:
    kind: Literal["dependency_blocked"] = "dependency_blocked"


_RECONCILIATION_PROOF_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class ReconciliationProof:
    """execution reconciliation 已验证的 commit/evidence 事实载体。"""

    task_id: str
    attempt: int
    commit_sha: str
    task_evidence_ref: dict[str, str]

    def __init__(
        self,
        task_id: str,
        attempt: int,
        commit_sha: str,
        task_evidence_ref: dict[str, str],
        *,
        _token: object,
    ) -> None:
        if _token is not _RECONCILIATION_PROOF_TOKEN:
            raise PlanStateValidationError(
                "ReconciliationProof 只能由 execution reconciliation 创建"
            )
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "attempt", attempt)
        object.__setattr__(self, "commit_sha", commit_sha)
        object.__setattr__(self, "task_evidence_ref", deepcopy(task_evidence_ref))

    @classmethod
    def _from_verified(
        cls,
        *,
        task_id: str,
        attempt: int,
        commit_sha: str,
        task_evidence_ref: dict[str, str],
    ) -> ReconciliationProof:
        return cls(
            task_id,
            attempt,
            commit_sha,
            task_evidence_ref,
            _token=_RECONCILIATION_PROOF_TOKEN,
        )


@dataclass(frozen=True, slots=True)
class ReconciledCommitEvent:
    proof: ReconciliationProof
    notes: str = ""
    kind: Literal["reconciled_commit"] = "reconciled_commit"


TransitionEvent = (
    AttemptStartedEvent
    | AttemptSucceededEvent
    | AttemptsExhaustedEvent
    | DependencyBlockedEvent
    | ReconciledCommitEvent
)


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def _error(report: LintReport, code: str, path: str, message: str) -> None:
    report.errors.append(LintIssue(code=code, path=path, message=message))


def _task_ids(plan: dict[str, Any], report: LintReport) -> list[str]:
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        _error(report, "PLAN-STATE-PLAN", "plan/tasks", "Plan tasks 必须为数组")
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        task_id = task.get("id") if isinstance(task, dict) else None
        if not isinstance(task_id, str):
            _error(
                report,
                "PLAN-STATE-PLAN",
                f"plan/tasks/{index}/id",
                "Plan task 必须有字符串 id",
            )
            continue
        if task_id in seen:
            _error(
                report,
                "PLAN-STATE-DUP-TASK",
                f"plan/tasks/{index}/id",
                f"Plan task id {task_id!r} 重复",
            )
        seen.add(task_id)
        ids.append(task_id)
    return ids


def _total_attempt_limit(config_snapshot: dict[str, Any], report: LintReport) -> int | None:
    budgets = config_snapshot.get("budgets")
    value = budgets.get("task_fix_attempts") if isinstance(budgets, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _error(
            report,
            "PLAN-STATE-CONFIG",
            "config_snapshot/budgets/task_fix_attempts",
            "task_fix_attempts 必须为非负整数",
        )
        return None
    return value + 1


def plan_state_snapshot_lint(
    plan: dict[str, Any],
    state: dict[str, Any],
    s4_seal: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> LintReport:
    """检查 Schema、S4/config 锚点、task 集合、attempt 上限与状态字段。"""
    report = LintReport()
    schema_errors = sorted(
        _validator().iter_errors(state),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    for issue in schema_errors:
        path = "/".join(str(part) for part in issue.absolute_path) or "<root>"
        _error(report, "PLAN-STATE-SCHEMA", path, issue.message)

    if plan.get("schema_version") != "3.0":
        _error(
            report,
            "PLAN-STATE-PLAN",
            "plan/schema_version",
            "Plan State 只允许绑定 Plan v3.0",
        )
    plan_ids = _task_ids(plan, report)
    plan_receipt = s4_seal.get("plan")
    if not isinstance(plan_receipt, dict):
        _error(report, "PLAN-STATE-SEAL", "s4_seal/plan", "缺少 S4 Plan receipt")
        plan_receipt = {}
    expected_plan_ref = {
        "path": plan_receipt.get("path"),
        "sha256": plan_receipt.get("sha256"),
    }
    if state.get("plan_ref") != expected_plan_ref:
        _error(
            report,
            "PLAN-STATE-PLAN-REF",
            "plan_ref",
            "plan_ref 必须逐字段等于 S4 seal 的 Plan receipt",
        )
    if plan_receipt.get("path") != "plan/plan.json":
        _error(
            report,
            "PLAN-STATE-SEAL",
            "s4_seal/plan/path",
            "S4 Plan receipt path 必须为 plan/plan.json",
        )
    actual_plan_sha256 = canonical_sha256(plan)
    if plan_receipt.get("sha256") != actual_plan_sha256:
        _error(
            report,
            "PLAN-STATE-PLAN-HASH",
            "s4_seal/plan/sha256",
            "S4 Plan receipt 与当前 Plan canonical SHA-256 不一致",
        )

    actual_config_sha256 = canonical_sha256(config_snapshot)
    if s4_seal.get("config_snapshot_sha256") != actual_config_sha256:
        _error(
            report,
            "PLAN-STATE-CONFIG-HASH",
            "s4_seal/config_snapshot_sha256",
            "S4 seal 与 config_snapshot canonical SHA-256 不一致",
        )

    state_tasks = state.get("tasks")
    state_ids: list[str] = []
    if isinstance(state_tasks, list):
        for task in state_tasks:
            if isinstance(task, dict) and isinstance(task.get("id"), str):
                state_ids.append(task["id"])
    if len(state_ids) != len(set(state_ids)):
        _error(
            report,
            "PLAN-STATE-DUP-TASK",
            "tasks",
            "Plan State task id 不得重复",
        )
    if set(state_ids) != set(plan_ids) or len(state_ids) != len(plan_ids):
        _error(
            report,
            "PLAN-STATE-TASK-SET",
            "tasks",
            "Plan State task id 集合必须与 Plan 完全相等",
        )

    total_limit = _total_attempt_limit(config_snapshot, report)
    if total_limit is not None and isinstance(state_tasks, list):
        for index, task in enumerate(state_tasks):
            if not isinstance(task, dict):
                continue
            attempts = task.get("attempts")
            if isinstance(attempts, bool) or not isinstance(attempts, int):
                continue
            if attempts > total_limit:
                _error(
                    report,
                    "PLAN-STATE-ATTEMPT-LIMIT",
                    f"tasks/{index}/attempts",
                    f"attempts {attempts} 超过 total_limit {total_limit}",
                )
            if task.get("status") == "blocked" and attempts != total_limit:
                _error(
                    report,
                    "PLAN-STATE-BLOCKED-ATTEMPTS",
                    f"tasks/{index}/attempts",
                    f"blocked task attempts 必须等于 total_limit {total_limit}",
                )
            evidence = task.get("acceptance_evidence")
            ref = evidence.get("task_evidence_ref") if isinstance(evidence, dict) else None
            if task.get("status") == "done" and isinstance(ref, dict):
                expected_path = (
                    f"test_results/task_evidence/{task.get('id')}/"
                    f"attempt_{attempts:03d}.json"
                )
                if ref.get("path") != expected_path:
                    _error(
                        report,
                        "PLAN-STATE-EVIDENCE-PATH",
                        f"tasks/{index}/acceptance_evidence/task_evidence_ref/path",
                        f"done task evidence path 必须为 {expected_path}",
                    )
    return report


def execution_state_lint(
    plan: dict[str, Any],
    state: dict[str, Any],
    workspace: GitOps,
    evidence_store: str | Path,
    stage_receipts: dict[str, Any],
    *,
    test_bundle: dict[str, Any] | None = None,
    require_clean: bool = True,
) -> LintReport:
    """5.2.5 第三项：核对 commit/trailer/祖先、证据内容、S5 锚点与工作区状态。

    只接收持久化事实：``workspace`` 是绑定生成仓库的 :class:`GitOps`，
    ``evidence_store`` 是本次 run 目录，``stage_receipts`` 是各阶段
    ``run.json.stages.<id>.output_refs``。需要完整执行对账时与
    :func:`plan_state_snapshot_lint` 联合运行；本函数不重复其纯 JSON 检查。
    """
    report = LintReport()
    run_dir = Path(evidence_store)
    if plan.get("schema_version") != "3.0":
        _error(report, "EXEC-PLAN-VERSION", "plan/schema_version", "执行对账只支持 Plan v3.0")
        return report
    plan_sha256 = canonical_sha256(plan)
    s4_refs = stage_receipts.get("s4") if isinstance(stage_receipts.get("s4"), dict) else {}
    plan_receipt = s4_refs.get("plan") if isinstance(s4_refs, dict) else None
    if not isinstance(plan_receipt, dict) or plan_receipt.get("sha256") != plan_sha256:
        _error(
            report,
            "EXEC-PLAN-SEAL",
            "stage_receipts/s4/plan",
            "S4 Plan receipt 缺失或与当前 Plan canonical SHA-256 不一致",
        )
    if state.get("plan_ref") != {
        "path": (plan_receipt or {}).get("path"),
        "sha256": (plan_receipt or {}).get("sha256"),
    }:
        _error(
            report,
            "EXEC-PLAN-REF",
            "plan_ref",
            "Plan State plan_ref 必须逐字段等于 S4 seal 的 Plan receipt",
        )
    if isinstance(s4_refs, dict) and s4_refs.get("delivery_blueprint_sha256") != plan.get(
        "delivery_blueprint_sha256"
    ):
        _error(
            report,
            "EXEC-BLUEPRINT-SEAL",
            "stage_receipts/s4/delivery_blueprint_sha256",
            "S4 seal 的 Blueprint hash 必须等于 Plan 顶层 delivery_blueprint_sha256",
        )

    head = _check_s5_receipts(report, plan, run_dir, stage_receipts, workspace)
    _check_task_commits(
        report,
        plan,
        state,
        workspace,
        run_dir,
        plan_sha256=plan_sha256,
        head=head,
        test_bundle=test_bundle,
    )
    if require_clean and not workspace.is_clean():
        _error(report, "EXEC-WORKSPACE-DIRTY", "workspace", "执行对账要求 workspace clean")
    return report


def _artifact_ref(
    report: LintReport,
    run_dir: Path,
    refs: dict[str, Any],
    stage: str,
    key: str,
) -> dict[str, Any] | None:
    """核对某阶段 receipt 指向的文件存在且内容 SHA-256 一致。"""
    ref = refs.get(key)
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        _error(
            report,
            "EXEC-STAGE-RECEIPT",
            f"stage_receipts/{stage}/{key}",
            f"{stage} 缺少合法 {key} receipt",
        )
        return None
    path = run_dir / str(ref["path"])
    if not path.is_file():
        _error(
            report,
            "EXEC-ARTIFACT-MISSING",
            f"stage_receipts/{stage}/{key}/path",
            f"receipt 指向的文件不存在: {ref['path']}",
        )
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != ref.get("sha256"):
        _error(
            report,
            "EXEC-ARTIFACT-HASH",
            f"stage_receipts/{stage}/{key}/sha256",
            f"{ref['path']} 的内容 SHA-256 与 receipt 不一致",
        )
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _error(
            report,
            "EXEC-ARTIFACT-INVALID",
            f"stage_receipts/{stage}/{key}/path",
            f"{ref['path']} 不是合法 JSON: {exc}",
        )
        return None
    return value if isinstance(value, dict) else None


def _check_s5_receipts(
    report: LintReport,
    plan: dict[str, Any],
    run_dir: Path,
    stage_receipts: dict[str, Any],
    workspace: GitOps,
) -> str | None:
    """6.5/6.6：S5 各 output ref 与 workspace 首提交必须仍锚定当前 HEAD。"""
    refs = stage_receipts.get("s5")
    if not isinstance(refs, dict):
        _error(report, "EXEC-STAGE-RECEIPT", "stage_receipts/s5", "缺少 S5 output_refs")
        return None
    blueprint_sha256 = plan.get("delivery_blueprint_sha256")
    for key in ("artifact_manifest", "contract_map"):
        value = _artifact_ref(report, run_dir, refs, "s5", key)
        if value is not None and value.get("delivery_blueprint_sha256") != blueprint_sha256:
            _error(
                report,
                "EXEC-BLUEPRINT-DRIFT",
                f"stage_receipts/s5/{key}",
                f"{key} 的 delivery_blueprint_sha256 与 Plan 不一致",
            )
    if "s5_summary" in refs:
        _artifact_ref(report, run_dir, refs, "s5", "s5_summary")
    first_commit = refs.get("workspace_head")
    if not isinstance(first_commit, str):
        _error(
            report,
            "EXEC-STAGE-RECEIPT",
            "stage_receipts/s5/workspace_head",
            "S5 必须封存 workspace 首提交 SHA",
        )
        return None
    head = workspace.head()
    if not workspace.is_ancestor(first_commit, head):
        _error(
            report,
            "EXEC-GIT-ANCESTRY",
            "stage_receipts/s5/workspace_head",
            "S5 首提交必须是当前 workspace HEAD 的祖先",
        )
    return head


def _check_task_commits(
    report: LintReport,
    plan: dict[str, Any],
    state: dict[str, Any],
    workspace: GitOps,
    run_dir: Path,
    *,
    plan_sha256: str,
    head: str | None,
    test_bundle: dict[str, Any] | None,
) -> None:
    """5.2.4/6.6：done task 的 commit trailer、证据内容与依赖祖先关系。"""
    plan_tasks = {
        task["id"]: task
        for task in plan.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    state_tasks = [task for task in state.get("tasks", []) if isinstance(task, dict)]
    commits: dict[str, str] = {}
    referenced: set[Path] = set()
    for index, task in enumerate(state_tasks):
        task_id = task.get("id")
        if task.get("status") != "done" or not isinstance(task_id, str):
            continue
        commit_sha = task.get("commit_sha")
        attempts = task.get("attempts")
        if not isinstance(commit_sha, str) or not isinstance(attempts, int):
            _error(
                report,
                "EXEC-DONE-INCOMPLETE",
                f"tasks/{index}",
                f"done task {task_id} 缺少 commit_sha 或 attempts",
            )
            continue
        try:
            metadata = workspace.task_commit_metadata(commit_sha)
        except GitError as exc:
            _error(
                report,
                "EXEC-COMMIT-TRAILER",
                f"tasks/{index}/commit_sha",
                f"{task_id}: 无法读取 task commit trailers: {exc}",
            )
            continue
        if metadata.task_id != task_id or metadata.attempt != attempts:
            _error(
                report,
                "EXEC-COMMIT-TRAILER",
                f"tasks/{index}/commit_sha",
                f"{task_id}: commit trailers 与 state 的 task/attempt 不一致",
            )
            continue
        commits[task_id] = metadata.commit_sha
        evidence_ref = (task.get("acceptance_evidence") or {}).get("task_evidence_ref")
        if not isinstance(evidence_ref, dict):
            _error(
                report,
                "EXEC-EVIDENCE-MISSING",
                f"tasks/{index}/acceptance_evidence",
                f"{task_id}: done task 必须携带 task evidence ref",
            )
            continue
        try:
            validated = validate_task_evidence_ref(
                run_dir,
                evidence_ref,
                task_id=task_id,
                attempt=attempts,
                plan_sha256=plan_sha256,
                workspace_tree=metadata.workspace_tree,
            )
        except TaskEvidenceValidationError as exc:
            _error(
                report,
                "EXEC-EVIDENCE-INVALID",
                f"tasks/{index}/acceptance_evidence/task_evidence_ref",
                f"{task_id}: {exc}",
            )
            continue
        referenced.add((run_dir / str(validated.ref["path"])).resolve())
        if validated.ref["sha256"] != metadata.evidence_sha256:
            _error(
                report,
                "EXEC-EVIDENCE-TRAILER",
                f"tasks/{index}/acceptance_evidence/task_evidence_ref/sha256",
                f"{task_id}: evidence SHA-256 与 commit trailer 不一致",
            )
        if head is not None and not workspace.is_ancestor(metadata.commit_sha, head):
            _error(
                report,
                "EXEC-GIT-ANCESTRY",
                f"tasks/{index}/commit_sha",
                f"{task_id}: task commit 必须是当前 HEAD 的祖先",
            )
        if test_bundle is not None:
            _check_summary_bundle_digests(
                report,
                run_dir,
                validated.value,
                test_bundle,
                path=f"tasks/{index}/acceptance_evidence",
                task_id=task_id,
            )

    for task_id, commit_sha in sorted(commits.items()):
        for dependency in plan_tasks.get(task_id, {}).get("depends_on", []):
            parent = commits.get(str(dependency))
            if parent is None:
                _error(
                    report,
                    "EXEC-DEPENDENCY-ORDER",
                    f"tasks/{task_id}/depends_on",
                    f"{task_id} 已 done，但依赖 {dependency} 没有已对账的 commit",
                )
            elif not workspace.is_ancestor(parent, commit_sha):
                _error(
                    report,
                    "EXEC-GIT-ANCESTRY",
                    f"tasks/{task_id}/depends_on",
                    f"{task_id} 的 commit 必须以依赖 {dependency} 的 commit 为祖先",
                )

    evidence_root = run_dir / "test_results" / "task_evidence"
    if evidence_root.is_dir():
        for path in sorted(evidence_root.rglob("*.json")):
            if path.resolve() not in referenced:
                _error(
                    report,
                    "EXEC-EVIDENCE-ORPHAN",
                    str(path.relative_to(run_dir)),
                    "存在没有任何 done task commit 引用的孤儿证据",
                )


def _check_summary_bundle_digests(
    report: LintReport,
    run_dir: Path,
    evidence: dict[str, Any],
    test_bundle: dict[str, Any],
    *,
    path: str,
    task_id: str,
) -> None:
    """5.3/5.4：验收证据引用的 Test Summary 必须绑定同一 Test Bundle 双摘要。"""
    manifest_ref = test_bundle.get("manifest_ref")
    expected = {
        "manifest_sha256": (
            manifest_ref.get("sha256") if isinstance(manifest_ref, dict) else None
        ),
        "bundle_tree_sha256": test_bundle.get("bundle_tree_sha256"),
    }
    for ref in evidence.get("test_summary_refs", []):
        relative = ref.get("path") if isinstance(ref, dict) else None
        if not isinstance(relative, str):
            continue
        summary_path = run_dir / relative
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _error(report, "EXEC-SUMMARY-INVALID", f"{path}: {relative}", f"{task_id}: {exc}")
            continue
        if not isinstance(summary, dict):
            continue
        for key, value in expected.items():
            if value is not None and summary.get(key) != value:
                _error(
                    report,
                    "EXEC-BUNDLE-DRIFT",
                    f"{path}: {relative}",
                    f"{task_id}: Test Summary 的 {key} 与冻结 Test Bundle 不一致",
                )


def initialize_plan_state(
    plan: dict[str, Any],
    s4_seal: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """用 Plan task 集合和 S4 seal 构造全 pending 的初始账本。"""
    tasks = plan.get("tasks")
    plan_receipt = s4_seal.get("plan")
    if not isinstance(tasks, list) or not isinstance(plan_receipt, dict):
        raise PlanStateValidationError("Plan tasks 与 S4 Plan receipt 必须存在")
    state = {
        "schema_version": "1.0",
        "plan_ref": {
            "path": plan_receipt.get("path"),
            "sha256": plan_receipt.get("sha256"),
        },
        "tasks": [
            {
                "id": task.get("id") if isinstance(task, dict) else None,
                "status": "pending",
                "attempts": 0,
                "notes": "",
                "commit_sha": None,
                "last_error": None,
                "acceptance_evidence": {"task_evidence_ref": None},
            }
            for task in tasks
        ],
    }
    report = plan_state_snapshot_lint(plan, state, s4_seal, config_snapshot)
    if not report.ok:
        details = "; ".join(
            f"{issue.code} {issue.path}: {issue.message}" for issue in report.errors
        )
        raise PlanStateValidationError(details)
    return state


def publish_initial_plan_state(
    path: str | Path,
    plan: dict[str, Any],
    s4_seal: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """拒绝覆盖已有账本，并以 canonical JSON 原子发布初始 Plan State。"""
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"Plan State 已存在，禁止重新初始化: {target}")
    state = initialize_plan_state(plan, s4_seal, config_snapshot)
    atomic_write_canonical_json(target, state)
    return state


def _plan_task(plan: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in plan.get("tasks", []):
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise PlanStateValidationError(f"Plan 中不存在 task {task_id!r}")


def _state_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in state.get("tasks", []):
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise PlanStateValidationError(f"Plan State 中不存在 task {task_id!r}")


def _blocked_dependencies(
    plan: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
) -> list[str]:
    task = _plan_task(plan, task_id)
    dependencies = task.get("depends_on", [])
    if not isinstance(dependencies, list):
        raise PlanStateValidationError(f"Plan task {task_id!r}.depends_on 必须为数组")
    blocked: list[str] = []
    for dependency_id in dependencies:
        if not isinstance(dependency_id, str):
            raise PlanStateValidationError(f"Plan task {task_id!r} 含非法 dependency id")
        dependency = _state_task(state, dependency_id)
        if dependency.get("status") in ("blocked", "blocked_by_dependency"):
            blocked.append(dependency_id)
    return sorted(blocked)


def _next_task_state(
    old: dict[str, Any],
    event: TransitionEvent,
    *,
    plan: dict[str, Any],
    state: dict[str, Any],
    total_limit: int,
) -> dict[str, Any]:
    task_id = old.get("id")
    status = old.get("status")
    if not isinstance(task_id, str):
        raise PlanStateValidationError("旧 task state 缺少字符串 id")
    if status in ("done", "blocked", "blocked_by_dependency"):
        raise PlanStateValidationError(f"终态 {status!r} 禁止继续迁移")
    new = deepcopy(old)

    if isinstance(event, AttemptStartedEvent):
        if status not in ("pending", "in_progress"):
            raise PlanStateValidationError("attempt_started 只允许 pending/in_progress")
        attempts = old.get("attempts")
        if not isinstance(attempts, int) or isinstance(attempts, bool):
            raise PlanStateValidationError("旧 attempts 必须为整数")
        if status == "pending" and event.previous_error is not None:
            raise PlanStateValidationError("首次 attempt_started 禁止 previous_error")
        if status == "in_progress" and not event.previous_error:
            raise PlanStateValidationError("重试 attempt_started 必须记录 previous_error 事件")
        if attempts >= total_limit:
            raise PlanStateValidationError("attempt 预算已耗尽，禁止开始下一次 attempt")
        new.update(
            {
                "status": "in_progress",
                "attempts": attempts + 1,
                "commit_sha": None,
                "last_error": None,
                "acceptance_evidence": {"task_evidence_ref": None},
            }
        )
        return new

    if status != "in_progress":
        raise PlanStateValidationError(f"{event.kind} 只允许从 in_progress 迁移")
    attempts = old.get("attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        raise PlanStateValidationError("旧 attempts 必须为整数")

    if isinstance(event, AttemptsExhaustedEvent):
        if attempts != total_limit:
            raise PlanStateValidationError(
                f"attempts_exhausted 要求 attempts={total_limit}，实际 {attempts}"
            )
        if not event.detail:
            raise PlanStateValidationError("attempts_exhausted.detail 不得为空")
        new.update(
            {
                "status": "blocked",
                "notes": event.notes,
                "commit_sha": None,
                "last_error": event.detail,
                "acceptance_evidence": {"task_evidence_ref": None},
            }
        )
        return new

    if isinstance(event, DependencyBlockedEvent):
        raise PlanStateValidationError("dependency_blocked 只允许从 pending 迁移")

    if isinstance(event, AttemptSucceededEvent):
        commit_sha = event.commit_sha
        evidence_ref = event.task_evidence_ref
        notes = event.notes
    elif isinstance(event, ReconciledCommitEvent):
        proof = event.proof
        if proof.task_id != task_id or proof.attempt != attempts:
            raise PlanStateValidationError("reconciliation proof 的 task/attempt 与状态不一致")
        commit_sha = proof.commit_sha
        evidence_ref = proof.task_evidence_ref
        notes = event.notes
    else:
        raise PlanStateValidationError(f"未知 transition event: {event!r}")
    new.update(
        {
            "status": "done",
            "notes": notes,
            "commit_sha": commit_sha,
            "last_error": None,
            "acceptance_evidence": {
                "task_evidence_ref": deepcopy(evidence_ref),
            },
        }
    )
    return new


def _dependency_blocked_state(
    old: dict[str, Any],
    *,
    plan: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    if old.get("status") != "pending":
        raise PlanStateValidationError("dependency_blocked 只允许从 pending 迁移")
    task_id = old.get("id")
    if not isinstance(task_id, str):
        raise PlanStateValidationError("旧 task state 缺少字符串 id")
    blocked = _blocked_dependencies(plan, state, task_id)
    if not blocked:
        raise PlanStateValidationError("没有 blocked 依赖，禁止标记 blocked_by_dependency")
    new = deepcopy(old)
    new.update(
        {
            "status": "blocked_by_dependency",
            "attempts": 0,
            "notes": "",
            "commit_sha": None,
            "last_error": f"blocked by dependency: {', '.join(blocked)}",
            "acceptance_evidence": {"task_evidence_ref": None},
        }
    )
    return new


def validate_state_transition(
    old: dict[str, Any],
    new: dict[str, Any],
    event: TransitionEvent,
    *,
    plan: dict[str, Any],
    state: dict[str, Any],
    total_limit: int,
) -> None:
    """按 5.2.4 验证单 task 迁移；不接受调用方自定义的额外字段变化。"""
    if total_limit < 1:
        raise PlanStateValidationError("total_limit 必须至少为 1")
    if old.get("id") != new.get("id"):
        raise PlanStateValidationError("状态迁移禁止改变 task id")
    expected = (
        _dependency_blocked_state(old, plan=plan, state=state)
        if isinstance(event, DependencyBlockedEvent)
        else _next_task_state(
            old,
            event,
            plan=plan,
            state=state,
            total_limit=total_limit,
        )
    )
    if new != expected:
        raise PlanStateValidationError("new task state 不等于事件确定性推导结果")


def transition_plan_state(
    path: str | Path,
    task_id: str,
    event: TransitionEvent,
    *,
    plan: dict[str, Any],
    s4_seal: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """加载、校验、迁移并原子写回完整 Plan State。

    调用方必须持有 run 互斥锁；函数始终以磁盘当前状态为旧值，避免调用方
    用陈旧内存副本覆盖其他 task 的已持久化进展。
    """
    target = Path(path)
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanStateValidationError(f"无法读取 Plan State: {exc}") from exc
    if not isinstance(state, dict):
        raise PlanStateValidationError("Plan State 顶层必须为 object")
    before = plan_state_snapshot_lint(plan, state, s4_seal, config_snapshot)
    if not before.ok:
        details = "; ".join(
            f"{issue.code} {issue.path}: {issue.message}" for issue in before.errors
        )
        raise PlanStateValidationError(f"旧 Plan State 非法: {details}")
    total_limit = _total_attempt_limit(config_snapshot, LintReport())
    if total_limit is None:
        raise PlanStateValidationError("config_snapshot 缺少合法 task_fix_attempts")

    old = _state_task(state, task_id)
    new = (
        _dependency_blocked_state(old, plan=plan, state=state)
        if isinstance(event, DependencyBlockedEvent)
        else _next_task_state(
            old,
            event,
            plan=plan,
            state=state,
            total_limit=total_limit,
        )
    )
    validate_state_transition(
        old,
        new,
        event,
        plan=plan,
        state=state,
        total_limit=total_limit,
    )
    updated = deepcopy(state)
    for index, task in enumerate(updated["tasks"]):
        if isinstance(task, dict) and task.get("id") == task_id:
            updated["tasks"][index] = new
            break
    after = plan_state_snapshot_lint(plan, updated, s4_seal, config_snapshot)
    if not after.ok:
        details = "; ".join(
            f"{issue.code} {issue.path}: {issue.message}" for issue in after.errors
        )
        raise PlanStateValidationError(f"新 Plan State 非法: {details}")
    atomic_write_canonical_json(target, updated)
    return updated
