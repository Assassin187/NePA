"""Trace 写入与成本核算（设计文档 5.5、8.4 要点 5）。

TraceWriter 按 5.5 格式向 trace/llm_calls.ndjson 追加一行/调用；
提示词与输出全文落盘到 prompts/、outputs/ 子目录（trace 行只存哈希与路径）；
成本按传入价格表（每百万 token 输入/输出单价）折算。价格表经构造函数传入，
禁止 import nepa.config。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nepa.llm.client import LLMRequest, LLMResponse

__all__ = ["ModelPricing", "TraceWriter", "compute_cost"]


@dataclass(frozen=True)
class ModelPricing:
    """每百万 token 的输入/输出单价（USD），对应 8.4 要点 5 的价格表条目。"""

    input_usd_per_mtok: float
    output_usd_per_mtok: float


def compute_cost(pricing: ModelPricing, tokens_in: int, tokens_out: int) -> float:
    """按价格表折算单次调用成本（USD）。"""
    cost = (
        tokens_in / 1_000_000 * pricing.input_usd_per_mtok
        + tokens_out / 1_000_000 * pricing.output_usd_per_mtok
    )
    return round(cost, 8)


class TraceWriter:
    """llm_calls.ndjson 写入器（5.5）。

    目录结构：<trace_dir>/llm_calls.ndjson、<trace_dir>/prompts/NNNNNN.txt、
    <trace_dir>/outputs/NNNNNN.{json,txt}。trace 行中的路径以 <trace_dir> 目录名
    为前缀（如 trace/prompts/000001.txt），与 4.4 运行目录布局一致。
    """

    def __init__(
        self,
        trace_dir: str | Path,
        run_id: str,
        pricing: Mapping[str, ModelPricing] | None = None,
    ) -> None:
        self._dir = Path(trace_dir)
        self._prompts_dir = self._dir / "prompts"
        self._outputs_dir = self._dir / "outputs"
        self._prompts_dir.mkdir(parents=True, exist_ok=True)
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "llm_calls.ndjson"
        self._run_id = run_id
        self._pricing = dict(pricing) if pricing else {}
        # 断点续跑时接续序号（4.8）
        self._seq = len(list(self._prompts_dir.glob("*.txt")))

    def cost_for(self, provider_name: str, model: str, tokens_in: int, tokens_out: int) -> float:
        """按价格表折算；键先查 "<model>" 再查 "<provider>/<model>"，缺失记 0。"""
        pricing = self._pricing.get(model) or self._pricing.get(f"{provider_name}/{model}")
        if pricing is None:
            return 0.0
        return compute_cost(pricing, tokens_in, tokens_out)

    def record(
        self,
        *,
        req: LLMRequest,
        resp: LLMResponse,
        provider_name: str,
        stage: str,
        attempt: int = 1,
        task_id: str | None = None,
        latency_ms: int = 0,
    ) -> dict[str, Any]:
        """写一行 trace（5.5）；就地更新 resp.cost_usd（缓存命中记 0，8.4 要点 4）。

        无结构化输出要求的调用 validation 记 null（5.5 的取值仅约束有校验的调用）。
        """
        self._seq += 1
        seq = f"{self._seq:06d}"

        # 提示词全文落盘（system + user）
        prompt_text = f"[system]\n{req.system}\n\n[user]\n{req.user}\n"
        prompt_file = self._prompts_dir / f"{seq}.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        # 输出全文落盘：结构化结果写 .json，否则原文写 .txt
        if resp.parsed is not None:
            output_file = self._outputs_dir / f"{seq}.json"
            output_file.write_text(
                json.dumps(resp.parsed, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            output_file = self._outputs_dir / f"{seq}.txt"
            output_file.write_text(resp.text, encoding="utf-8")

        cost = 0.0 if resp.cached else self.cost_for(
            provider_name, resp.model, resp.tokens_in, resp.tokens_out
        )
        resp.cost_usd = cost

        prefix = self._dir.name  # 通常为 "trace"（4.4）
        line: dict[str, Any] = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": self._run_id,
            "stage": stage,
            "agent_role": req.role,
            "task_id": task_id,
            "attempt": attempt,
            "model": f"{provider_name}/{resp.model}",
            "params": {"temperature": req.temperature, "max_tokens": req.max_tokens},
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "prompt_path": f"{prefix}/prompts/{prompt_file.name}",
            "output_path": f"{prefix}/outputs/{output_file.name}",
            "tokens_in": resp.tokens_in,
            "tokens_out": resp.tokens_out,
            "cost_usd": cost,
            "latency_ms": latency_ms,
            "cached": resp.cached,
            "validation": resp.validation,
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return line
