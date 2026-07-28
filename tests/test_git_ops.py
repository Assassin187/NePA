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
        ops.commit_task("T1", "implement codec", ["allowed.c"])

    assert ops.head() == previous_head
    assert set(_git(workspace, "status", "--porcelain").splitlines()) == {
        " M allowed.c",
        " M unrelated.c",
    }


def test_commit_task_stages_and_commits_only_task_whitelist(tmp_path: Path) -> None:
    workspace, ops = _workspace(tmp_path)
    (workspace / "allowed.c").write_text("allowed change\n", encoding="utf-8")

    new_head = ops.commit_task("T1", "implement codec", ["allowed.c"])

    assert new_head == ops.head()
    assert ops.is_clean()
    assert ops.log_subjects()[0] == "T1: implement codec"
    assert _git(workspace, "show", "--format=", "--name-only", "HEAD") == "allowed.c"
