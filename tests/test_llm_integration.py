"""LLM provider 集成测试（真实 API，设计文档 8.4）。

需要环境变量 DS_API（DeepSeek API key）；未设置时跳过。
运行：DS_API=sk-... .venv/bin/python -m pytest tests/test_llm_integration.py -q -m integration -s
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from nepa.llm.client import LLMRequest
from nepa.llm.providers.openai_compat import OpenAICompatProvider

pytestmark = pytest.mark.integration


@pytest.mark.skipif("DS_API" not in os.environ, reason="DS_API 未设置，跳过真实 API 集成测试")
def test_deepseek_minimal_structured_output() -> None:
    """对 https://api.deepseek.com 的 deepseek-chat 发一次极小结构化输出请求验证连通性。"""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    provider = OpenAICompatProvider(
        base_url="https://api.deepseek.com",
        api_key=os.environ["DS_API"],
        model="deepseek-chat",
        name="deepseek",
        timeout_s=60.0,
    )
    try:
        resp = provider.complete(
            LLMRequest(
                role="smoke_test",
                system="You are a helpful assistant.",
                user='Reply with exactly the JSON object {"ok": true}.',
                json_schema=schema,
                temperature=0.0,
                max_tokens=64,
            )
        )
    finally:
        provider.close()

    assert resp.parsed is not None
    assert resp.parsed["ok"] is True
    assert resp.validation in ("pass", "repaired")
    assert resp.tokens_in > 0 and resp.tokens_out > 0
    # -s 运行时打印 token 消耗，供 notes 报告
    print(
        f"\n[integration] model={resp.model} validation={resp.validation} "
        f"tokens_in={resp.tokens_in} tokens_out={resp.tokens_out}"
    )
