"""LLM 响应缓存（设计文档 8.4 要点 4）。

键 = sha256(provider + model + 参数 + 完整提示词)；文件存储（一响应一 JSON 文件）；
命中时 cached=true、成本记 0。用于重放调试（4.8）与消融实验省钱。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from nepa.canonical import canonical_json_bytes
from nepa.llm.client import LLMRequest, LLMResponse

__all__ = ["ResponseCache"]


class ResponseCache:
    """文件系统响应缓存：<cache_dir>/<sha256>.json。"""

    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(provider: str, model: str, req: LLMRequest) -> str:
        """sha256(provider+model+参数+完整提示词)，含 json_schema（8.4 要点 4）。"""
        payload = {
            "provider": provider,
            "model": model,
            "params": {"temperature": req.temperature, "max_tokens": req.max_tokens},
            "system": req.system,
            "user": req.user,
            "json_schema": req.json_schema,
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> LLMResponse | None:
        """命中返回 cached=true、cost_usd=0 的响应；未命中返回 None。"""
        path = self._path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        resp = LLMResponse.model_validate(data)
        resp.cached = True
        resp.cost_usd = 0.0
        resp.latency_ms = 0
        return resp

    def put(self, key: str, resp: LLMResponse) -> None:
        """原子写入（tmp + rename），与 run_store 一致的幂等风格（4.8）。"""
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(resp.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
