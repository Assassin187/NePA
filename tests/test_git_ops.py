"""生成 workspace 的 Git 提交边界测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nepa.tools.git_ops import GitError, GitOps


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.rstrip()


def _workspace(tmp_path: Path) -> tuple[Path, GitOps]:
    workspace = tmp_path / "generated"
    workspace.mkdir()
    (workspace / "allowed.c").write_text("before\n", encoding="utf-8")
    (workspace / "unrelated.c").write_text("before\n", encoding="utf-8")
    ops = GitOps(workspace)
    ops.init_and_commit()
    return workspace, ops


def test_commit_task_rejects_changes_outside_deliverable_files(tmp_path: Path) -> None:
    workspace, ops = _workspace(tmp_path)
    previous_head = ops.head()
    (workspace / "allowed.c").write_text("allowed change\n", encoding="utf-8")
    (workspace / "unrelated.c").write_text("unrelated change\n", encoding="utf-8")

    with pytest.raises(GitError, match="unrelated.c"):
        ops.prepare_task_commit(["allowed.c"])

    assert ops.head() == previous_head
    assert set(_git(workspace, "status", "--porcelain").splitlines()) == {
        " M allowed.c",
        " M unrelated.c",
    }


def test_commit_task_stages_and_commits_only_task_whitelist(tmp_path: Path) -> None:
    workspace, ops = _workspace(tmp_path)
    (workspace / "allowed.c").write_text("allowed change\n", encoding="utf-8")

    prepared = ops.prepare_task_commit(["allowed.c"])
    new_head = ops.commit_prepared_task(
        prepared,
        task_id="T-001",
        title="implement codec",
        attempt=2,
        evidence_sha256="ab" * 32,
    )

    assert new_head == ops.head()
    assert ops.is_clean()
    assert ops.log_subjects()[0] == "T-001: implement codec"
    assert _git(workspace, "show", "--format=", "--name-only", "HEAD") == "allowed.c"
    metadata = ops.task_commit_metadata(new_head)
    assert metadata.workspace_tree == prepared.workspace_tree
    assert metadata.task_id == "T-001"
    assert metadata.attempt == 2
    assert metadata.evidence_sha256 == "ab" * 32


def test_commit_task_allows_subset_when_other_whitelisted_file_never_existed(
    tmp_path: Path,
) -> None:
    workspace, ops = _workspace(tmp_path)
    (workspace / "allowed.c").write_text("subset delivery\n", encoding="utf-8")

    prepared = ops.prepare_task_commit(["allowed.c", "future.c"])

    assert prepared.allowed_paths == ("allowed.c", "future.c")
    assert _git(workspace, "diff", "--cached", "--name-only") == "allowed.c"


def test_commit_rejects_worktree_change_after_evidence_tree_was_prepared(
    tmp_path: Path,
) -> None:
    workspace, ops = _workspace(tmp_path)
    (workspace / "allowed.c").write_text("prepared content\n", encoding="utf-8")
    prepared = ops.prepare_task_commit(["allowed.c"])
    (workspace / "allowed.c").write_text("changed after evidence\n", encoding="utf-8")

    with pytest.raises(GitError, match="未暂存"):
        ops.commit_prepared_task(
            prepared,
            task_id="T-001",
            title="implement codec",
            attempt=1,
            evidence_sha256="ab" * 32,
        )

    assert ops.log_subjects()[0] == "scaffold: deterministic project skeleton"


def test_partial_task_trailers_are_corruption_not_baseline(tmp_path: Path) -> None:
    workspace, ops = _workspace(tmp_path)
    (workspace / "allowed.c").write_text("after\n", encoding="utf-8")
    _git(workspace, "add", "allowed.c")
    _git(
        workspace,
        "commit",
        "-q",
        "-m",
        "broken task commit",
        "-m",
        "NePA-Task: T-001",
    )

    with pytest.raises(GitError, match="不完整"):
        ops.has_task_commit_metadata(ops.head())
