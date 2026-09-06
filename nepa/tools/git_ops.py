"""Small, deterministic git boundary for the single E0 checkpoint."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable


class GitOperationError(RuntimeError):
    """The E0 checkpoint could not be created or verified."""


def _run(workspace: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitOperationError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def checkpoint_workspace(workspace: str | Path, paths: Iterable[str], *, plan_version: str = "1.0.0", epoch: str = "E0") -> dict[str, str | bool]:
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    initialized = (root / ".git").exists()
    if not initialized:
        _run(root, ["init", "--quiet"])
        _run(root, ["config", "user.name", "NePA Materialization"])
        _run(root, ["config", "user.email", "materialization@nepa.invalid"])
    status = _run(root, ["status", "--porcelain", "--untracked-files=all"])
    if status:
        unexpected = [line[3:] for line in status.splitlines() if len(line) >= 4]
        allowed = set(paths)
        if any(path not in allowed for path in unexpected):
            raise GitOperationError("workspace contains an unrecorded path before E0 checkpoint")
    path_list = sorted(set(paths), key=lambda value: value.encode("utf-8"))
    if not path_list:
        raise GitOperationError("E0 checkpoint cannot be empty")
    _run(root, ["add", "--", *path_list])
    staged = _run(root, ["diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
    if set(staged.splitlines()) != set(path_list):
        raise GitOperationError("E0 staged tree does not equal the rendered path set")
    has_commit = subprocess.run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=root, capture_output=True, text=True, check=False).returncode == 0
    if has_commit:
        raise GitOperationError("workspace already has a checkpoint; recovery must verify and reuse it")
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "NePA Materialization", "GIT_AUTHOR_EMAIL": "materialization@nepa.invalid", "GIT_COMMITTER_NAME": "NePA Materialization", "GIT_COMMITTER_EMAIL": "materialization@nepa.invalid"})
    message = f"Materialize {plan_version} {epoch}\n\nNePA-Plan: {plan_version}\nNePA-Epoch: {epoch}\n"
    result = subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=root, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        raise GitOperationError(f"git commit failed: {result.stderr.strip()}")
    commit_sha = _run(root, ["rev-parse", "HEAD"])
    tree_sha = _run(root, ["rev-parse", "HEAD^{tree}"])
    body = _run(root, ["show", "-s", "--format=%B", "HEAD"])
    if f"NePA-Plan: {plan_version}" not in body or f"NePA-Epoch: {epoch}" not in body:
        raise GitOperationError("E0 checkpoint trailers are missing")
    return {"commit_sha": commit_sha, "tree_sha": tree_sha, "initialized": initialized}


def verify_checkpoint(workspace: str | Path, checkpoint: dict[str, str]) -> None:
    root = Path(workspace).resolve()
    if _run(root, ["rev-parse", "HEAD"]) != checkpoint["commit_sha"] or _run(root, ["rev-parse", "HEAD^{tree}"]) != checkpoint["tree_sha"]:
        raise GitOperationError("E0 checkpoint commit or tree drifted")
    body = _run(root, ["show", "-s", "--format=%B", "HEAD"])
    if "NePA-Plan: 1.0.0" not in body or "NePA-Epoch: E0" not in body:
        raise GitOperationError("E0 checkpoint trailers drifted")


__all__ = ["GitOperationError", "checkpoint_workspace", "verify_checkpoint"]
