"""确定性文件工具（设计文档 4.2 L4、4.8、6.6.3）。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class UnsafePathError(ValueError):
    """LLM 输出路径越过允许的 workspace/白名单边界。"""


def sha256_file(path: str | Path) -> str:
    """流式计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: str | Path) -> Any:
    """读取 UTF-8 JSON。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write_text(path: str | Path, content: str) -> None:
    """同目录临时文件 + fsync + os.replace 原子写入。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f"{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str | Path, value: Any) -> None:
    """以稳定的人读格式原子写入 JSON。"""
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def resolve_workspace_path(root: str | Path, relative: str) -> Path:
    """解析 workspace 相对路径并拒绝绝对路径、``..`` 与目录逃逸。"""
    if not relative or Path(relative).is_absolute():
        raise UnsafePathError(f"文件路径必须是非空 workspace 相对路径: {relative!r}")
    parts = Path(relative).parts
    if ".." in parts:
        raise UnsafePathError(f"文件路径禁止包含 '..': {relative!r}")
    workspace = Path(root).resolve()
    target = (workspace / relative).resolve()
    if not target.is_relative_to(workspace):
        raise UnsafePathError(f"文件路径越过 workspace: {relative!r}")
    return target


def write_allowed_files(
    root: str | Path,
    files: list[dict[str, Any]],
    allowed: set[str],
) -> list[Path]:
    """校验完整文件输出契约与白名单后原子写入（6.6.1/6.6.3）。"""
    seen: set[str] = set()
    prepared: list[tuple[Path, str]] = []
    for item in files:
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise TypeError("Coder files 条目必须包含字符串 path/content")
        if path not in allowed:
            raise UnsafePathError(f"Coder 输出了任务白名单外文件: {path}")
        if path in seen:
            raise ValueError(f"Coder 重复输出文件: {path}")
        seen.add(path)
        prepared.append((resolve_workspace_path(root, path), content))
    if not prepared:
        raise ValueError("Coder 必须输出至少一个完整文件")
    for target, content in prepared:
        atomic_write_text(target, content)
    return [target for target, _ in prepared]
