"""NePA 单实现可复现的 canonical JSON 与原子工件发布。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _reject_non_string_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical JSON object key at {path} must be str")
            _reject_non_string_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_non_string_keys(item, f"{path}[{index}]")


def canonical_json_bytes(value: Any) -> bytes:
    """按项目冻结算法编码 JSON；不追加换行。

    int 原样编码；float 使用 CPython shortest-repr。NaN/Infinity 与非字符串
    object key 显式拒绝。此算法只承诺本 Python 实现可复现，不是 RFC 8785/JCS。
    """
    _reject_non_string_keys(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """计算内存 JSON 对象 canonical 字节的 SHA-256。"""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def atomic_write_canonical_json(path: str | Path, value: Any) -> None:
    """把 NePA 自产 JSON 工件以 canonical 字节原子发布。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=target.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
