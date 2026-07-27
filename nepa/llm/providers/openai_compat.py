"""OpenAI 兼容 provider（设计文档 8.4 要点 1）。

覆盖任何 OpenAI 兼容端点：OpenAI、DeepSeek、Qwen、Kimi、vLLM 自部署等，
只换 base_url。参数全部经构造函数传入（禁止 import nepa.config）。
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

__all__ = ["OpenAICompatProvider"]


class OpenAICompatProvider(StructuredProvider):
    """POST {base_url}/chat/completions 的 httpx 实现。

    - base_url 例：https://api.deepseek.com 或 https://api.openai.com/v1（8.3 示例）。
    - 网络/5xx/429 指数退避重试 ≤ max_retries 次（8.4 要点 3）。
    - native_json_mode=True 时附带 response_format={"type": "json_object"}；
      schema 仍由基类内嵌提示词（8.4 要点 2）。
    """

    supports_native_json = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        name: str = "openai_compat",
        timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_base_delay_s: float = 0.5,
        native_json_mode: bool = True,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,  # 测试注入 MockTransport
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.model = model
        self.supports_native_json = native_json_mode
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        if extra_headers:
            self._headers.update(extra_headers)
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _raw_complete(
        self, *, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool
    ) -> RawResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = request_with_retries(
            lambda: self._client.post(self._url, json=payload, headers=self._headers),
            max_retries=self._max_retries,
            base_delay_s=self._retry_base_delay_s,
            sleep=self._sleep,
        )
        if resp.status_code >= 400:  # 4xx（非 429）不可重试，直接上报
            raise ProviderHTTPError(resp.status_code, resp.text[:500])

        data = resp.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        usage = data.get("usage") or {}
        return RawResult(
            text=text,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=str(data.get("model", self.model)),
        )
