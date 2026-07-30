"""生成 workspace 的 git 工具（设计文档 6.5、6.6，L4）。"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from nepa.tools.fs_ops import resolve_workspace_path


class GitError(RuntimeError):
    """生成 workspace 的 git 操作失败。"""


@dataclass(frozen=True)
class GitResult:
    stdout: str
    stderr: str
    returncode: int = 0


@dataclass(frozen=True, slots=True)
class PreparedTaskCommit:
    allowed_paths: tuple[str, ...]
    workspace_tree: str


@dataclass(frozen=True, slots=True)
class TaskCommitMetadata:
    commit_sha: str
    workspace_tree: str
    task_id: str
    attempt: int
    evidence_sha256: str


class GitOps:
    """只允许操作构造时绑定的生成 workspace。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def _run(self, args: Sequence[str], *, check: bool = True) -> GitResult:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return GitResult(proc.stdout, proc.stderr, proc.returncode)

    def init_and_commit(self, message: str = "scaffold: deterministic project skeleton") -> str:
        self._run(["init", "-q"])
        self._run(["config", "user.name", "NePA"])
        self._run(["config", "user.email", "nepa@localhost"])
        self._run(["add", "--all"])
        self._run(["commit", "-q", "-m", message])
        return self.head()

    def _changed_paths(self) -> set[str]:
        """返回 index/worktree 中全部变更路径，rename/copy 的两端都计入。"""
        raw = self._run(["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout
        records = raw.split("\0")
        paths: set[str] = set()
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 4 or record[2] != " ":
                raise GitError(f"无法解析 git status 记录: {record!r}")
            status = record[:2]
            paths.add(record[3:])
            if "R" in status or "C" in status:
                if index >= len(records) or not records[index]:
                    raise GitError(f"git status 的 rename/copy 记录缺少源路径: {record!r}")
                paths.add(records[index])
                index += 1
        return paths

    def _allowed_task_paths(self, deliverable_files: Sequence[str]) -> set[str]:
        allowed: set[str] = set()
        for relative in deliverable_files:
            target = resolve_workspace_path(self.workspace, relative)
            allowed.add(target.relative_to(self.workspace).as_posix())
        if not allowed:
            raise GitError("任务 deliverable_files 不能为空")
        return allowed

    def prepare_task_commit(
        self,
        deliverable_files: Sequence[str],
    ) -> PreparedTaskCommit:
        """白名单检查并 stage task 变更，返回供 evidence 绑定的 git tree。"""
        allowed = self._allowed_task_paths(deliverable_files)
        changed = self._changed_paths()
        unexpected = sorted(changed - allowed)
        if unexpected:
            raise GitError("检测到任务白名单外变更，拒绝提交: " + ", ".join(unexpected))
        if not changed:
            raise GitError("任务白名单内没有可提交的变更")

        # ``deliverable_files`` is a capability boundary, not a promise that every
        # allowed path already exists. Passing the whole whitelist to ``git add``
        # makes a legal subset delivery fail when an allowed path has never been
        # created. Stage only the status-proven change set after the boundary check.
        self._run(["add", "--all", "--", *sorted(changed)])
        unstaged = self._run(["diff", "--name-only"]).stdout.splitlines()
        if unstaged:
            raise GitError("stage 后仍有未暂存变更: " + ", ".join(sorted(unstaged)))
        staged = set(self._run(["diff", "--cached", "--name-only"]).stdout.splitlines())
        if not staged or not staged <= allowed:
            raise GitError("staged task 路径为空或越出 deliverable_files")
        tree = self._run(["write-tree"]).stdout.strip()
        return PreparedTaskCommit(tuple(sorted(allowed)), tree)

    def commit_prepared_task(
        self,
        prepared: PreparedTaskCommit,
        *,
        task_id: str,
        title: str,
        attempt: int,
        evidence_sha256: str,
    ) -> str:
        """提交已由 evidence 绑定的 staged tree，并写三项固定 trailer。"""
        if not re.fullmatch(r"T-[0-9]{3,}", task_id):
            raise GitError(f"非法 task id: {task_id!r}")
        if attempt < 1:
            raise GitError("attempt 必须至少为 1")
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise GitError("evidence_sha256 必须为 64 位小写十六进制")
        allowed = set(prepared.allowed_paths)
        changed = self._changed_paths()
        unexpected = sorted(changed - allowed)
        if unexpected:
            raise GitError("prepare 后出现白名单外变更: " + ", ".join(unexpected))
        unstaged = self._run(["diff", "--name-only"]).stdout.splitlines()
        if unstaged:
            raise GitError("prepare 后出现未暂存变更: " + ", ".join(sorted(unstaged)))
        current_tree = self._run(["write-tree"]).stdout.strip()
        if current_tree != prepared.workspace_tree:
            raise GitError("staged tree 在 evidence 发布后发生变化")
        trailers = "\n".join(
            (
                f"NePA-Task: {task_id}",
                f"NePA-Attempt: {attempt}",
                f"NePA-Evidence-SHA256: {evidence_sha256}",
            )
        )
        self._run(["commit", "-q", "-m", f"{task_id}: {title}", "-m", trailers])
        commit_sha = self.head()
        if self.commit_tree(commit_sha) != prepared.workspace_tree:
            raise GitError("task commit tree 与 evidence 绑定 tree 不一致")
        return commit_sha

    def commit_tree(self, commit_sha: str = "HEAD") -> str:
        return self._run(["show", "-s", "--format=%T", commit_sha]).stdout.strip()

    def _trailer_value(self, commit_sha: str, key: str) -> str:
        values = self._trailer_values(commit_sha, key)
        if len(values) != 1:
            raise GitError(f"commit {commit_sha} 必须恰有一个 {key} trailer")
        return values[0]

    def _trailer_values(self, commit_sha: str, key: str) -> list[str]:
        raw = self._run(
            ["show", "-s", f"--format=%(trailers:key={key},valueonly)", commit_sha]
        ).stdout
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def has_task_commit_metadata(self, commit_sha: str) -> bool:
        """无 task trailers 返回 False；部分存在或重复均视为损坏。"""
        keys = ("NePA-Task", "NePA-Attempt", "NePA-Evidence-SHA256")
        counts = [len(self._trailer_values(commit_sha, key)) for key in keys]
        if counts == [0, 0, 0]:
            return False
        if counts != [1, 1, 1]:
            raise GitError(f"commit {commit_sha} 的 NePA task trailers 不完整或重复")
        return True

    def task_commit_metadata(self, commit_sha: str) -> TaskCommitMetadata:
        """读取并严格解析 task commit 的 tree 与三项固定 trailers。"""
        task_id = self._trailer_value(commit_sha, "NePA-Task")
        attempt_raw = self._trailer_value(commit_sha, "NePA-Attempt")
        evidence_sha256 = self._trailer_value(
            commit_sha,
            "NePA-Evidence-SHA256",
        )
        if not re.fullmatch(r"T-[0-9]{3,}", task_id):
            raise GitError("NePA-Task trailer 非法")
        try:
            attempt = int(attempt_raw)
        except ValueError as exc:
            raise GitError("NePA-Attempt trailer 必须为整数") from exc
        if attempt < 1:
            raise GitError("NePA-Attempt trailer 必须至少为 1")
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise GitError("NePA-Evidence-SHA256 trailer 非法")
        return TaskCommitMetadata(
            commit_sha=self._run(["rev-parse", commit_sha]).stdout.strip(),
            workspace_tree=self.commit_tree(commit_sha),
            task_id=task_id,
            attempt=attempt,
            evidence_sha256=evidence_sha256,
        )

    def head(self) -> str:
        return self._run(["rev-parse", "HEAD"]).stdout.strip()

    def has_commit(self) -> bool:
        return bool(self._run(["rev-parse", "--verify", "HEAD"], check=False).stdout.strip())

    def commit_count(self) -> int:
        raw = self._run(["rev-list", "--count", "HEAD"]).stdout.strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise GitError(f"无法解析 commit 数量: {raw!r}") from exc

    def is_clean(self) -> bool:
        return not self._run(["status", "--porcelain"]).stdout.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """6.6：判断 ancestor 是否为 descendant 的祖先（同一 commit 也算）。"""
        result = self._run(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        )
        return result.returncode == 0

    def restore_files(self, relative_paths: Sequence[str]) -> None:
        """恢复失败 attempt，仅触碰任务白名单文件（6.6.1）。"""
        for relative in relative_paths:
            target = resolve_workspace_path(self.workspace, relative)
            tracked = self._run(["ls-files", "--error-unmatch", "--", relative], check=False)
            if tracked.stdout.strip():
                self._run(["restore", "--staged", "--worktree", "--", relative])
            elif target.is_file() or target.is_symlink():
                target.unlink()

    def log_subjects(self) -> list[str]:
        raw = self._run(["log", "--format=%s"]).stdout
        return [line for line in raw.splitlines() if line]
