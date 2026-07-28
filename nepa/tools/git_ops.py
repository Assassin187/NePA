"""生成 workspace 的 git 工具（设计文档 6.5、6.6，L4）。"""

from __future__ import annotations

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
        return GitResult(proc.stdout, proc.stderr)

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

    def commit_task(
        self,
        task_id: str,
        title: str,
        deliverable_files: Sequence[str],
    ) -> str:
        """只提交任务 ``deliverable_files``；发现白名单外变更立即拒绝。"""
        allowed: set[str] = set()
        for relative in deliverable_files:
            target = resolve_workspace_path(self.workspace, relative)
            allowed.add(target.relative_to(self.workspace).as_posix())
        if not allowed:
            raise GitError("任务 deliverable_files 不能为空")

        changed = self._changed_paths()
        unexpected = sorted(changed - allowed)
        if unexpected:
            raise GitError("检测到任务白名单外变更，拒绝提交: " + ", ".join(unexpected))
        if not changed:
            raise GitError("任务白名单内没有可提交的变更")

        self._run(["add", "--all", "--", *sorted(allowed)])
        self._run(["commit", "-q", "-m", f"{task_id}: {title}"])
        return self.head()

    def head(self) -> str:
        return self._run(["rev-parse", "HEAD"]).stdout.strip()

    def has_commit(self) -> bool:
        return bool(self._run(["rev-parse", "--verify", "HEAD"], check=False).stdout.strip())

    def is_clean(self) -> bool:
        return not self._run(["status", "--porcelain"]).stdout.strip()

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
