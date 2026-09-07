"""Small, deterministic git boundary for the single E0 checkpoint."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


class GitOperationError(RuntimeError):
    """The E0 checkpoint could not be created or verified."""


def _run(workspace: Path, args: list[str], *, env: Mapping[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True, check=False, env=dict(env) if env is not None else None)
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


def commit_task(
    workspace: str | Path,
    paths: Iterable[str],
    *,
    task_id: str,
    task_uid: str,
    attempt: int,
    evidence_seq: int,
    evidence_sha256: str,
    plan_version: str = "1.0.0",
    epoch: str = "E0",
) -> dict[str, str]:
    """Create exactly one ordinary task commit from the requested changed set."""

    root = Path(workspace).resolve()
    selected = sorted(set(paths), key=lambda value: value.encode("utf-8"))
    if not selected:
        raise GitOperationError("normal task commit requires at least one changed path")
    if any(not value or Path(value).is_absolute() or ".." in Path(value).parts for value in selected):
        raise GitOperationError("task commit contains an unsafe path")
    staged_before = _run(root, ["diff", "--cached", "--name-only"]).splitlines()
    if staged_before:
        raise GitOperationError("workspace contains unrelated staged changes before task commit")
    _run(root, ["add", "--", *selected])
    staged = _run(root, ["diff", "--cached", "--name-only"]).splitlines()
    if set(staged) != set(selected):
        raise GitOperationError("task commit staged set does not equal the accepted changed set")
    message = (
        f"Execute {task_id} {plan_version} {epoch}\n\n"
        f"NePA-Task: {task_id}\nNePA-Task-UID: {task_uid}\n"
        f"NePA-Plan: {plan_version}\nNePA-Epoch: {epoch}\n"
        f"NePA-Attempt: {attempt}\nNePA-Evidence-Seq: {evidence_seq}\n"
        f"NePA-Evidence-SHA256: {evidence_sha256}\n"
    )
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "NePA S6 Executor", "GIT_AUTHOR_EMAIL": "executor@nepa.invalid", "GIT_COMMITTER_NAME": "NePA S6 Executor", "GIT_COMMITTER_EMAIL": "executor@nepa.invalid"})
    result = subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=root, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        raise GitOperationError(f"git task commit failed: {result.stderr.strip()}")
    commit_sha = _run(root, ["rev-parse", "HEAD"])
    tree_sha = _run(root, ["rev-parse", "HEAD^{tree}"])
    required = (
        f"NePA-Task: {task_id}", f"NePA-Task-UID: {task_uid}", f"NePA-Plan: {plan_version}", f"NePA-Epoch: {epoch}", f"NePA-Attempt: {attempt}",
        f"NePA-Evidence-Seq: {evidence_seq}", f"NePA-Evidence-SHA256: {evidence_sha256}",
    )
    if any(_run(root, ["show", "-s", f"--format=%(trailers:key={value.split(':', 1)[0]},valueonly)", "HEAD"]).splitlines() != [value.split(": ", 1)[1]] for value in required):
        raise GitOperationError("task commit trailers are incomplete")
    return {"commit_sha": commit_sha, "tree_sha": tree_sha}


def prepare_task_commit(
    workspace: str | Path,
    files: Mapping[str, bytes],
    *,
    task_id: str,
    task_uid: str,
    attempt: int,
    evidence_seq: int,
    evidence_sha256: str,
    plan_version: str = "1.0.0",
    epoch: str = "E0",
) -> dict[str, str]:
    """Create the exact commit object without moving HEAD or the live index."""

    root = Path(workspace).resolve()
    selected = sorted(files, key=lambda value: value.encode("utf-8"))
    if not selected or any(not value or Path(value).is_absolute() or ".." in Path(value).parts for value in selected):
        raise GitOperationError("normal task commit requires a non-empty safe changed set")
    parent = _run(root, ["rev-parse", "HEAD"])
    fd, index_name = tempfile.mkstemp(prefix="nepa-s6-index-")
    os.close(fd)
    os.unlink(index_name)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = index_name
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    identity = {
        "GIT_AUTHOR_NAME": "NePA S6 Executor", "GIT_AUTHOR_EMAIL": "executor@nepa.invalid",
        "GIT_COMMITTER_NAME": "NePA S6 Executor", "GIT_COMMITTER_EMAIL": "executor@nepa.invalid",
        "GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp,
    }
    env.update(identity)
    message = (
        f"Execute {task_id} {plan_version} {epoch}\n\n"
        f"NePA-Task: {task_id}\nNePA-Task-UID: {task_uid}\n"
        f"NePA-Plan: {plan_version}\nNePA-Epoch: {epoch}\n"
        f"NePA-Attempt: {attempt}\nNePA-Evidence-Seq: {evidence_seq}\n"
        f"NePA-Evidence-SHA256: {evidence_sha256}\n"
    )
    try:
        _run(root, ["read-tree", parent], env=env)
        for relative in selected:
            proc = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=root, input=files[relative],
                capture_output=True, check=False,
            )
            if proc.returncode != 0:
                raise GitOperationError(f"git hash-object failed: {proc.stderr.decode(errors='replace').strip()}")
            blob = proc.stdout.decode("ascii").strip()
            _run(root, ["update-index", "--add", "--cacheinfo", "100644", blob, relative], env=env)
        tree = _run(root, ["write-tree"], env=env)
        result = subprocess.run(
            ["git", "commit-tree", tree, "-p", parent], cwd=root, input=message,
            capture_output=True, text=True, check=False, env=env,
        )
        if result.returncode != 0:
            raise GitOperationError(f"git commit-tree failed: {result.stderr.strip()}")
        commit = result.stdout.strip()
    finally:
        try:
            os.unlink(index_name)
        except FileNotFoundError:
            pass
    return {"commit_sha": commit, "tree_sha": tree, "parent_sha": parent, "message": message, "timestamp": timestamp}


def publish_task_commit(workspace: str | Path, paths: Iterable[str], prepared: Mapping[str, str]) -> dict[str, str]:
    """Install a prepared commit as HEAD after checking the live staged tree."""

    root = Path(workspace).resolve()
    selected = sorted(set(paths), key=lambda value: value.encode("utf-8"))
    if not selected or _run(root, ["rev-parse", "HEAD"]) != prepared["parent_sha"]:
        raise GitOperationError("prepared task commit parent or changed set drifted")
    if _run(root, ["diff", "--cached", "--name-only"]):
        raise GitOperationError("workspace contains unrelated staged changes before publication")
    _run(root, ["add", "--", *selected])
    if _run(root, ["write-tree"]) != prepared["tree_sha"]:
        raise GitOperationError("live staged tree disagrees with prepared task commit")
    _run(root, ["update-ref", "HEAD", prepared["commit_sha"], prepared["parent_sha"]])
    if _run(root, ["rev-parse", "HEAD^{tree}"]) != prepared["tree_sha"]:
        raise GitOperationError("published task commit tree drifted")
    return {"commit_sha": prepared["commit_sha"], "tree_sha": prepared["tree_sha"]}


def prepare_joint_commit(
    workspace: str | Path,
    files: Mapping[str, bytes],
    *,
    verification_id: str,
    joint_evidence_sha256: str,
) -> dict[str, str]:
    """Prepare one commit for a complete F1 member change set."""
    root = Path(workspace).resolve()
    selected = sorted(files, key=lambda value: value.encode("utf-8"))
    if not selected or any(not value or Path(value).is_absolute() or ".." in Path(value).parts for value in selected):
        raise GitOperationError("joint commit requires a non-empty safe changed set")
    parent = _run(root, ["rev-parse", "HEAD"])
    fd, index_name = tempfile.mkstemp(prefix="nepa-s6-joint-index-")
    os.close(fd)
    os.unlink(index_name)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = index_name
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    env.update({"GIT_AUTHOR_NAME": "NePA S6 Executor", "GIT_AUTHOR_EMAIL": "executor@nepa.invalid", "GIT_COMMITTER_NAME": "NePA S6 Executor", "GIT_COMMITTER_EMAIL": "executor@nepa.invalid", "GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp})
    message = f"Execute joint lease {verification_id}\n\nNePA-Verification-ID: {verification_id}\nNePA-Joint-Evidence-SHA256: {joint_evidence_sha256}\n"
    try:
        _run(root, ["read-tree", parent], env=env)
        for relative in selected:
            proc = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root, input=files[relative], capture_output=True, check=False)
            if proc.returncode != 0:
                raise GitOperationError(f"git hash-object failed: {proc.stderr.decode(errors='replace').strip()}")
            _run(root, ["update-index", "--add", "--cacheinfo", "100644", proc.stdout.decode("ascii").strip(), relative], env=env)
        tree = _run(root, ["write-tree"], env=env)
        result = subprocess.run(["git", "commit-tree", tree, "-p", parent], cwd=root, input=message, capture_output=True, text=True, check=False, env=env)
        if result.returncode != 0:
            raise GitOperationError(f"git commit-tree failed: {result.stderr.strip()}")
        commit = result.stdout.strip()
    finally:
        try:
            os.unlink(index_name)
        except FileNotFoundError:
            pass
    return {"commit_sha": commit, "tree_sha": tree, "parent_sha": parent, "message": message, "timestamp": timestamp}


def publish_joint_commit(workspace: str | Path, paths: Iterable[str], prepared: Mapping[str, str]) -> dict[str, str]:
    """Publish a prepared joint commit after checking the exact live tree."""
    root = Path(workspace).resolve()
    selected = sorted(set(paths), key=lambda value: value.encode("utf-8"))
    if not selected or _run(root, ["rev-parse", "HEAD"]) != prepared["parent_sha"]:
        raise GitOperationError("prepared joint commit parent or changed set drifted")
    if _run(root, ["diff", "--cached", "--name-only"]):
        raise GitOperationError("workspace contains unrelated staged changes before joint commit")
    _run(root, ["add", "--", *selected])
    if _run(root, ["write-tree"]) != prepared["tree_sha"]:
        raise GitOperationError("live staged tree disagrees with prepared joint commit")
    _run(root, ["update-ref", "HEAD", prepared["commit_sha"], prepared["parent_sha"]])
    if _run(root, ["rev-parse", "HEAD^{tree}"]) != prepared["tree_sha"]:
        raise GitOperationError("published joint commit tree drifted")
    for key in ("NePA-Verification-ID", "NePA-Joint-Evidence-SHA256"):
        expected = prepared["message"].split(f"{key}: ", 1)[1].splitlines()[0]
        if _run(root, ["show", "-s", f"--format=%(trailers:key={key},valueonly)", "HEAD"]).splitlines() != [expected]:
            raise GitOperationError("joint commit trailers are incomplete")
    return {"commit_sha": prepared["commit_sha"], "tree_sha": prepared["tree_sha"]}


__all__ = ["GitOperationError", "checkpoint_workspace", "commit_task", "prepare_joint_commit", "prepare_task_commit", "publish_joint_commit", "publish_task_commit", "verify_checkpoint"]
