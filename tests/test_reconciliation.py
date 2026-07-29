"""S6 commit/tree/trailer/evidence 联合 reconciliation 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nepa.canonical import (
    atomic_write_canonical_json,
    canonical_sha256,
)
from nepa.plan_state import AttemptStartedEvent, publish_initial_plan_state, transition_plan_state
from nepa.reconciliation import (
    ReconciliationError,
    audit_done_tasks,
    build_reconciliation_proof,
    discover_in_progress_reconciliation,
    reconcile_in_progress_task,
    validate_done_task,
)
from nepa.task_evidence import publish_task_evidence
from nepa.tools.git_ops import GitOps


def _build_ref(run_dir: Path) -> dict[str, str]:
    path = run_dir / "test_results" / "build" / "T-001-attempt-001.json"
    atomic_write_canonical_json(path, {"variant": "release", "ok": True})
    return {
        "path": path.relative_to(run_dir).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _workspace(tmp_path: Path) -> tuple[Path, GitOps]:
    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "codec.c").write_text("before\n", encoding="utf-8")
    git = GitOps(workspace)
    git.init_and_commit()
    return workspace, git


def test_validated_commit_and_evidence_create_reconciliation_proof(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    workspace, git = _workspace(tmp_path)
    (workspace / "codec.c").write_text("after\n", encoding="utf-8")
    prepared = git.prepare_task_commit(["codec.c"])
    evidence = publish_task_evidence(
        run_dir,
        task_id="T-001",
        attempt=1,
        plan_sha256="ab" * 32,
        workspace_tree=prepared.workspace_tree,
        build_result_refs=[_build_ref(run_dir)],
        test_summary_refs=[],
    )
    commit_sha = git.commit_prepared_task(
        prepared,
        task_id="T-001",
        title="implement codec",
        attempt=1,
        evidence_sha256=evidence.ref["sha256"],
    )

    proof = build_reconciliation_proof(git, commit_sha, evidence)

    assert proof.task_id == "T-001"
    assert proof.attempt == 1
    assert proof.commit_sha == commit_sha
    assert proof.task_evidence_ref == evidence.ref

    in_progress = {
        "id": "T-001",
        "status": "in_progress",
        "attempts": 1,
    }
    assert (
        discover_in_progress_reconciliation(
            git,
            run_dir,
            in_progress,
            plan_sha256="ab" * 32,
        )
        == proof
    )
    done = {
        **in_progress,
        "status": "done",
        "commit_sha": commit_sha,
        "acceptance_evidence": {"task_evidence_ref": evidence.ref},
    }
    assert validate_done_task(git, run_dir, done, plan_sha256="ab" * 32) == proof


def test_wrong_evidence_trailer_cannot_create_proof(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    workspace, git = _workspace(tmp_path)
    (workspace / "codec.c").write_text("after\n", encoding="utf-8")
    prepared = git.prepare_task_commit(["codec.c"])
    evidence = publish_task_evidence(
        run_dir,
        task_id="T-001",
        attempt=1,
        plan_sha256="ab" * 32,
        workspace_tree=prepared.workspace_tree,
        build_result_refs=[_build_ref(run_dir)],
        test_summary_refs=[],
    )
    commit_sha = git.commit_prepared_task(
        prepared,
        task_id="T-001",
        title="implement codec",
        attempt=1,
        evidence_sha256="00" * 32,
    )

    with pytest.raises(ReconciliationError, match="evidence_sha256"):
        build_reconciliation_proof(git, commit_sha, evidence)


def test_baseline_head_without_task_trailers_is_not_forward_commit(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _workspace_path, git = _workspace(tmp_path)

    proof = discover_in_progress_reconciliation(
        git,
        run_dir,
        {"id": "T-001", "status": "in_progress", "attempts": 1},
        plan_sha256="ab" * 32,
    )

    assert proof is None


def test_matching_forward_commit_requires_clean_workspace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    workspace, git = _workspace(tmp_path)
    (workspace / "codec.c").write_text("after\n", encoding="utf-8")
    prepared = git.prepare_task_commit(["codec.c"])
    evidence = publish_task_evidence(
        run_dir,
        task_id="T-001",
        attempt=1,
        plan_sha256="ab" * 32,
        workspace_tree=prepared.workspace_tree,
        build_result_refs=[_build_ref(run_dir)],
        test_summary_refs=[],
    )
    git.commit_prepared_task(
        prepared,
        task_id="T-001",
        title="implement codec",
        attempt=1,
        evidence_sha256=evidence.ref["sha256"],
    )
    (workspace / "codec.c").write_text("dirty after commit\n", encoding="utf-8")

    with pytest.raises(ReconciliationError, match="clean"):
        discover_in_progress_reconciliation(
            git,
            run_dir,
            {"id": "T-001", "status": "in_progress", "attempts": 1},
            plan_sha256="ab" * 32,
        )


def test_done_audit_rechecks_evidence_source_receipts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    workspace, git = _workspace(tmp_path)
    (workspace / "codec.c").write_text("after\n", encoding="utf-8")
    prepared = git.prepare_task_commit(["codec.c"])
    build_ref = _build_ref(run_dir)
    evidence = publish_task_evidence(
        run_dir,
        task_id="T-001",
        attempt=1,
        plan_sha256="ab" * 32,
        workspace_tree=prepared.workspace_tree,
        build_result_refs=[build_ref],
        test_summary_refs=[],
    )
    commit_sha = git.commit_prepared_task(
        prepared,
        task_id="T-001",
        title="implement codec",
        attempt=1,
        evidence_sha256=evidence.ref["sha256"],
    )
    (run_dir / build_ref["path"]).write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256"):
        validate_done_task(
            git,
            run_dir,
            {
                "id": "T-001",
                "status": "done",
                "attempts": 1,
                "commit_sha": commit_sha,
                "acceptance_evidence": {"task_evidence_ref": evidence.ref},
            },
            plan_sha256="ab" * 32,
        )


def test_commit_before_state_window_is_atomically_forward_reconciled(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    workspace, git = _workspace(tmp_path)
    plan = {
        "schema_version": "3.0",
        "tasks": [{"id": "T-001", "depends_on": []}],
    }
    config = {"budgets": {"task_fix_attempts": 3}}
    seal = {
        "plan": {
            "path": "plan/plan.json",
            "sha256": canonical_sha256(plan),
        },
        "config_snapshot_sha256": canonical_sha256(config),
    }
    state_path = run_dir / "plan" / "plan_state.json"
    publish_initial_plan_state(state_path, plan, seal, config)
    transition_plan_state(
        state_path,
        "T-001",
        AttemptStartedEvent(),
        plan=plan,
        s4_seal=seal,
        config_snapshot=config,
    )
    (workspace / "codec.c").write_text("after\n", encoding="utf-8")
    prepared = git.prepare_task_commit(["codec.c"])
    evidence = publish_task_evidence(
        run_dir,
        task_id="T-001",
        attempt=1,
        plan_sha256=seal["plan"]["sha256"],
        workspace_tree=prepared.workspace_tree,
        build_result_refs=[_build_ref(run_dir)],
        test_summary_refs=[],
    )
    commit_sha = git.commit_prepared_task(
        prepared,
        task_id="T-001",
        title="implement codec",
        attempt=1,
        evidence_sha256=evidence.ref["sha256"],
    )

    assert reconcile_in_progress_task(
        git,
        run_dir,
        state_path,
        "T-001",
        plan=plan,
        s4_seal=seal,
        config_snapshot=config,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    task = state["tasks"][0]
    assert task["status"] == "done"
    assert task["commit_sha"] == commit_sha
    assert task["acceptance_evidence"]["task_evidence_ref"] == evidence.ref
    proofs = audit_done_tasks(
        git,
        run_dir,
        state,
        plan_sha256=seal["plan"]["sha256"],
    )
    assert proofs["T-001"].commit_sha == commit_sha
