"""S6 commit/tree/trailer/Task Evidence 的联合 reconciliation 校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nepa.plan_state import (
    ReconciledCommitEvent,
    ReconciliationProof,
    transition_plan_state,
)
from nepa.task_evidence import (
    ValidatedTaskEvidence,
    task_evidence_relative_path,
    validate_task_evidence_ref,
)
from nepa.tools.git_ops import GitOps


class ReconciliationError(ValueError):
    """commit 与不可变 Task Evidence 无法形成完整前向恢复证明。"""


def build_reconciliation_proof(
    git: GitOps,
    commit_sha: str,
    evidence: ValidatedTaskEvidence,
) -> ReconciliationProof:
    """联合验证 commit tree/trailers 和 evidence 后生成类型化 proof。"""
    metadata = git.task_commit_metadata(commit_sha)
    value = evidence.value
    expected = {
        "task_id": value.get("task_id"),
        "attempt": value.get("attempt"),
        "workspace_tree": value.get("workspace_tree"),
        "evidence_sha256": evidence.ref.get("sha256"),
    }
    actual = {
        "task_id": metadata.task_id,
        "attempt": metadata.attempt,
        "workspace_tree": metadata.workspace_tree,
        "evidence_sha256": metadata.evidence_sha256,
    }
    mismatches = [key for key in expected if expected[key] != actual[key]]
    if mismatches:
        raise ReconciliationError(
            "task commit 与 evidence 不一致: " + ", ".join(mismatches)
        )
    return ReconciliationProof._from_verified(
        task_id=metadata.task_id,
        attempt=metadata.attempt,
        commit_sha=metadata.commit_sha,
        task_evidence_ref=evidence.ref,
    )


def validate_done_task(
    git: GitOps,
    run_dir: str | Path,
    task_state: dict[str, object],
    *,
    plan_sha256: str,
) -> ReconciliationProof:
    """反向审计 done state 声称的 commit/evidence 完整闭环。"""
    if task_state.get("status") != "done":
        raise ReconciliationError("validate_done_task 只接受 done 状态")
    task_id = task_state.get("id")
    attempt = task_state.get("attempts")
    commit_sha = task_state.get("commit_sha")
    evidence = task_state.get("acceptance_evidence")
    evidence_ref = (
        evidence.get("task_evidence_ref") if isinstance(evidence, dict) else None
    )
    if (
        not isinstance(task_id, str)
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or not isinstance(commit_sha, str)
        or not isinstance(evidence_ref, dict)
    ):
        raise ReconciliationError("done state 缺少 task/attempt/commit/evidence")
    metadata = git.task_commit_metadata(commit_sha)
    validated = validate_task_evidence_ref(
        run_dir,
        evidence_ref,
        task_id=task_id,
        attempt=attempt,
        plan_sha256=plan_sha256,
        workspace_tree=metadata.workspace_tree,
    )
    proof = build_reconciliation_proof(git, commit_sha, validated)
    if proof.commit_sha != commit_sha:
        raise ReconciliationError("done state commit_sha 必须保存完整 commit id")
    return proof


def discover_in_progress_reconciliation(
    git: GitOps,
    run_dir: str | Path,
    task_state: dict[str, object],
    *,
    plan_sha256: str,
) -> ReconciliationProof | None:
    """发现 HEAD 上 commit-before-state 窗口；普通 baseline 返回 None。"""
    if task_state.get("status") != "in_progress":
        raise ReconciliationError("forward reconciliation 只接受 in_progress 状态")
    task_id = task_state.get("id")
    attempt = task_state.get("attempts")
    if (
        not isinstance(task_id, str)
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
    ):
        raise ReconciliationError("in_progress state 缺少合法 task/attempt")
    head = git.head()
    if not git.has_task_commit_metadata(head):
        return None
    metadata = git.task_commit_metadata(head)
    if metadata.task_id != task_id:
        return None
    if metadata.attempt != attempt:
        raise ReconciliationError("HEAD task trailer attempt 与 in_progress state 不一致")
    if not git.is_clean():
        raise ReconciliationError("匹配 task commit 后 workspace 必须 clean")
    evidence_ref = {
        "path": task_evidence_relative_path(task_id, attempt),
        "sha256": metadata.evidence_sha256,
    }
    validated = validate_task_evidence_ref(
        run_dir,
        evidence_ref,
        task_id=task_id,
        attempt=attempt,
        plan_sha256=plan_sha256,
        workspace_tree=metadata.workspace_tree,
    )
    return build_reconciliation_proof(git, head, validated)


def audit_done_tasks(
    git: GitOps,
    run_dir: str | Path,
    state: dict[str, object],
    *,
    plan_sha256: str,
) -> dict[str, ReconciliationProof]:
    """反向审计 State 中全部 done task，返回逐 task 完整 proof。"""
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise ReconciliationError("Plan State tasks 必须为数组")
    proofs: dict[str, ReconciliationProof] = {}
    for task in tasks:
        if not isinstance(task, dict) or task.get("status") != "done":
            continue
        proof = validate_done_task(git, run_dir, task, plan_sha256=plan_sha256)
        if proof.task_id in proofs:
            raise ReconciliationError(f"重复 done task id: {proof.task_id}")
        proofs[proof.task_id] = proof
    return proofs


def reconcile_in_progress_task(
    git: GitOps,
    run_dir: str | Path,
    state_path: str | Path,
    task_id: str,
    *,
    plan: dict[str, Any],
    s4_seal: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> bool:
    """若命中 commit-before-state 窗口，原子前向迁移为 done。"""
    path = Path(state_path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"无法读取 Plan State: {exc}") from exc
    if not isinstance(state, dict):
        raise ReconciliationError("Plan State 顶层必须为 object")
    tasks = state.get("tasks")
    task_state = next(
        (
            task
            for task in tasks
            if isinstance(task, dict) and task.get("id") == task_id
        ),
        None,
    ) if isinstance(tasks, list) else None
    if not isinstance(task_state, dict):
        raise ReconciliationError(f"Plan State 中不存在 task {task_id!r}")
    plan_receipt = s4_seal.get("plan")
    plan_sha256 = (
        plan_receipt.get("sha256") if isinstance(plan_receipt, dict) else None
    )
    if not isinstance(plan_sha256, str):
        raise ReconciliationError("S4 seal 缺少 Plan SHA-256")
    proof = discover_in_progress_reconciliation(
        git,
        run_dir,
        task_state,
        plan_sha256=plan_sha256,
    )
    if proof is None:
        return False
    transition_plan_state(
        path,
        task_id,
        ReconciledCommitEvent(proof, notes="forward reconciled after commit-before-state"),
        plan=plan,
        s4_seal=s4_seal,
        config_snapshot=config_snapshot,
    )
    return True
