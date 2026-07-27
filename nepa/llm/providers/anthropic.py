"""Anthropic 原生 messages API provider（设计文档 8.4 要点 1）。

无通用的原生 JSON 模式，结构化输出走基类的 schema 内嵌提示词退化路径
（8.4 要点 2，行为与 openai_compat 一致）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from nepa.llm.client import (
    ProviderHTTPError,
    RawResult,
    StructuredProvider,
    request_with_retries,
)

__all__ = ["AnthropicProvider"]

_DEFAULT_VERSION = "2023-06-01"  # anthropic-version 请求头


class AnthropicProvider(StructuredProvider):
    """POST {base_url}/v1/messages 的 httpx 实现。

    网络/5xx/429（含 529 overloaded）指数退避重试 ≤ max_retries 次（8.4 要点 3）。
    """

    supports_native_json = False

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = "https://api.anthropic.com",
        name: str = "anthropic",
        timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_base_delay_s: float = 0.5,
        anthropic_version: str = _DEFAULT_VERSION,
        transport: httpx.BaseTransport | None = None,  # 测试注入 MockTransport
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": anthropic_version,
        }
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _raw_complete(
        self, *, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool
    ) -> RawResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            payload["system"] = system

        resp = request_with_retries(
            lambda: self._client.post(self._url, json=payload, headers=self._headers),
            max_retries=self._max_retries,
            base_delay_s=self._retry_base_delay_s,
            sleep=self._sleep,
        )
        if resp.status_code >= 400:
            raise ProviderHTTPError(resp.status_code, resp.text[:500])

        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return RawResult(
            text=text,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            model=str(data.get("model", self.model)),
        )
