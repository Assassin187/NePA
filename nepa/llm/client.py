"""LLM Provider 统一抽象层（设计文档 8.4）。

本模块只依赖基本类型与 httpx/jsonschema/pydantic：
- LLMRequest / LLMResponse / Provider 协议：签名按 8.4。
- StructuredProvider：结构化输出统一策略基类（8.4 要点 2，P8）：
  优先 provider 原生 JSON 模式（同时把 schema 内嵌提示词，保证模型知道目标结构），
  不支持原生模式则纯 schema 内嵌；抽取首个 JSON 对象容错剥壳；jsonschema 校验
  失败自动发一次修复调用，仍失败抛 StructuredOutputError。
- request_with_retries：网络/5xx/429 指数退避重试 ≤ 3 次（8.4 要点 3）。
- LLMClient：组合 provider + 缓存 + telemetry（8.4 要点 4/5）。

禁止 import nepa.config：一切参数经构造函数传入。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
import jsonschema
from pydantic import BaseModel

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "Provider",
    "ProviderHTTPError",
    "RawResult",
    "RetryExhaustedError",
    "StructuredOutputError",
    "StructuredProvider",
    "extract_first_json",
    "request_with_retries",
    "schema_errors",
]


# ---------------------------------------------------------------- 异常


class LLMError(Exception):
    """LLM 抽象层错误基类。"""


class ProviderHTTPError(LLMError):
    """不可重试的 HTTP 错误（4xx 非 429 等）。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"provider HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class RetryExhaustedError(LLMError):
    """网络/5xx/429 指数退避重试 ≤3 次后仍失败（8.4 要点 3）。"""


class StructuredOutputError(LLMError):
    """结构化输出经一次修复调用后仍未通过 schema 校验（8.4 要点 2）。

    携带最后一次响应（validation="fail"），便于上层落 trace（5.5）。
    """

    def __init__(self, errors: list[str], response: LLMResponse | None = None) -> None:
        super().__init__(
            "structured output failed schema validation after repair: " + "; ".join(errors[:5])
        )
        self.errors = errors
        self.response = response


# ---------------------------------------------------------------- 数据模型（8.4）


class LLMRequest(BaseModel):
    """统一请求。字段按 8.4 简化签名。"""

    role: str  # 角色名，用于 trace 与路由（4.6）
    system: str = ""
    user: str
    json_schema: dict[str, Any] | None = None  # 非空则要求结构化输出
    temperature: float = 0.0
    max_tokens: int = 4096


class LLMResponse(BaseModel):
    """统一响应。tokens 计入修复调用（若发生）。

    validation/latency_ms 为 trace（5.5）所需的扩展字段。
    """

    text: str
    parsed: dict[str, Any] | None = None  # 校验通过的 JSON（若要求）
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0  # 由 telemetry 按价格表折算（8.4 要点 5）
    model: str = ""
    cached: bool = False
    validation: Literal["pass", "repaired", "fail"] | None = None  # 5.5
    latency_ms: int = 0


@runtime_checkable
class Provider(Protocol):
    """Provider 协议（8.4）。新 provider 只需实现 complete。"""

    def complete(self, req: LLMRequest) -> LLMResponse: ...


# ---------------------------------------------------------------- JSON 容错剥壳与校验


def extract_first_json(text: str) -> dict[str, Any]:
    """抽取文本中首个 JSON 对象（8.4 要点 2 的容错剥壳）。

    从首个 '{' 起逐个位置尝试 raw_decode，自然跳过 markdown 代码围栏与前后缀散文。
    找不到 JSON 对象时抛 ValueError。
    """
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text, idx)
        except ValueError:
            pass
        else:
            if isinstance(obj, dict):
                return obj
        idx = text.find("{", idx + 1)
    raise ValueError("model output contains no JSON object")


def schema_errors(instance: Any, schema: dict[str, Any], limit: int = 20) -> list[str]:
    """返回 jsonschema 校验错误清单（空表示通过）。"""
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    errors: list[str] = []
    for err in validator.iter_errors(instance):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
        if len(errors) >= limit:
            break
    return errors


# 8.8：模板正文英文，维护注释中文。
_SCHEMA_INSTRUCTION = (
    "\n\nYour reply MUST be a single JSON object that conforms to the JSON Schema below."
    " Do not include any text outside the JSON object.\n\nJSON Schema:\n{schema}\n"
)

_REPAIR_PROMPT = (
    "Your previous reply failed JSON Schema validation.\n\n"
    "Previous reply:\n{previous}\n\n"
    "Validation errors:\n{errors}\n\n"
    "Return a corrected single JSON object that conforms to the JSON Schema below."
    " Output only the JSON object.\n\nJSON Schema:\n{schema}\n"
)


def embed_schema_prompt(user: str, schema: dict[str, Any]) -> str:
    """schema 内嵌提示词（8.4 要点 2 的退化路径；原生 JSON 模式下也内嵌以传达目标结构）。"""
    return user + _SCHEMA_INSTRUCTION.format(
        schema=json.dumps(schema, ensure_ascii=False, indent=2)
    )


def build_repair_prompt(previous_text: str, errors: list[str], schema: dict[str, Any]) -> str:
    """把错误清单馈给模型的修复提示词（8.4 要点 2）。"""
    return _REPAIR_PROMPT.format(
        previous=previous_text,
        errors="\n".join(f"- {e}" for e in errors),
        schema=json.dumps(schema, ensure_ascii=False, indent=2),
    )


# ---------------------------------------------------------------- HTTP 重试（8.4 要点 3）


def _retryable_status(code: int) -> bool:
    return code == 429 or code >= 500


def request_with_retries(
    send: Callable[[], httpx.Response],
    *,
    max_retries: int = 3,
    base_delay_s: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """网络错误/5xx/429 指数退避重试 ≤ max_retries 次；其余状态码原样返回。

    重试不计入阶段预算——区分模型失败与基础设施失败（8.4 要点 3）。
    """
    attempt = 0
    while True:
        try:
            resp = send()
        except httpx.TransportError as exc:
            if attempt >= max_retries:
                raise RetryExhaustedError(
                    f"transport error after {attempt + 1} attempts: {exc}"
                ) from exc
        else:
            if not _retryable_status(resp.status_code):
                return resp
            if attempt >= max_retries:
                raise RetryExhaustedError(
                    f"HTTP {resp.status_code} after {attempt + 1} attempts: {resp.text[:200]}"
                )
        sleep(base_delay_s * (2**attempt))
        attempt += 1


# ---------------------------------------------------------------- 结构化输出基类（8.4 要点 2）


@dataclass
class RawResult:
    """provider 原始补全结果（HTTP 层产物，未做结构化处理）。"""

    text: str
    tokens_in: int
    tokens_out: int
    model: str


class StructuredProvider(ABC):
    """统一结构化输出策略基类：行为对所有 provider 一致（8.4 要点 2）。

    子类只需实现 _raw_complete（一次 HTTP 补全，含自身的重试逻辑）。
    """

    name: str = "provider"
    model: str = ""
    supports_native_json: bool = False  # True 时 _raw_complete 收到 json_mode=True

    @abstractmethod
    def _raw_complete(
        self, *, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool
    ) -> RawResult: ...

    def complete(self, req: LLMRequest) -> LLMResponse:
        if req.json_schema is None:
            raw = self._raw_complete(
                system=req.system,
                user=req.user,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                json_mode=False,
            )
            return LLMResponse(
                text=raw.text,
                parsed=None,
                tokens_in=raw.tokens_in,
                tokens_out=raw.tokens_out,
                model=raw.model,
                validation=None,
            )

        # 优先原生 JSON 模式；schema 始终内嵌提示词以传达目标结构（8.4 要点 2）
        json_mode = self.supports_native_json
        user = embed_schema_prompt(req.user, req.json_schema)
        raw = self._raw_complete(
            system=req.system,
            user=user,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            json_mode=json_mode,
        )
        tokens_in, tokens_out = raw.tokens_in, raw.tokens_out
        parsed, errors = self._try_parse(raw.text, req.json_schema)
        if not errors:
            return LLMResponse(
                text=raw.text,
                parsed=parsed,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                model=raw.model,
                validation="pass",
            )

        # 校验失败：自动发一次修复调用（把错误清单馈给模型）
        repair_user = build_repair_prompt(raw.text, errors, req.json_schema)
        raw2 = self._raw_complete(
            system=req.system,
            user=repair_user,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            json_mode=json_mode,
        )
        tokens_in += raw2.tokens_in
        tokens_out += raw2.tokens_out
        parsed2, errors2 = self._try_parse(raw2.text, req.json_schema)
        if not errors2:
            return LLMResponse(
                text=raw2.text,
                parsed=parsed2,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                model=raw2.model,
                validation="repaired",
            )

        # 仍失败：向上报错，携带 fail 响应供 trace 落盘（5.5）
        fail_resp = LLMResponse(
            text=raw2.text,
            parsed=None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=raw2.model,
            validation="fail",
        )
        raise StructuredOutputError(errors2, fail_resp)

    @staticmethod
    def _try_parse(text: str, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """剥壳 + 校验；返回 (通过的对象或 None, 错误清单)。"""
        try:
            obj = extract_first_json(text)
        except ValueError as exc:
            return None, [str(exc)]
        errs = schema_errors(obj, schema)
        return (obj, []) if not errs else (None, errs)


# ---------------------------------------------------------------- 组合器（缓存 + telemetry）


class CacheLike(Protocol):
    """响应缓存协议（实现见 nepa.llm.cache，8.4 要点 4）。"""

    def make_key(self, provider: str, model: str, req: LLMRequest) -> str: ...
    def get(self, key: str) -> LLMResponse | None: ...
    def put(self, key: str, resp: LLMResponse) -> None: ...


class TraceSink(Protocol):
    """trace 写入协议（实现见 nepa.llm.telemetry，5.5）。"""

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
    ) -> dict[str, Any]: ...


class LLMClient:
    """provider + 缓存 + telemetry 的组合调用器。

    - 缓存命中：cached=true、成本记 0（8.4 要点 4）。
    - 成本由 telemetry 按价格表折算并写入 trace（8.4 要点 5、5.5）。
    """

    def __init__(
        self,
        provider: Provider,
        *,
        provider_name: str,
        model: str | None = None,
        cache: CacheLike | None = None,
        trace: TraceSink | None = None,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model if model is not None else str(getattr(provider, "model", ""))
        self._cache = cache
        self._trace = trace

    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
    ) -> LLMResponse:
        key: str | None = None
        resp: LLMResponse | None = None
        if self._cache is not None:
            key = self._cache.make_key(self._provider_name, self._model, req)
            resp = self._cache.get(key)

        if resp is None:
            t0 = time.perf_counter()
            try:
                resp = self._provider.complete(req)
            except StructuredOutputError as exc:
                # fail 响应也落 trace（5.5 validation=fail），再向上抛
                if self._trace is not None and exc.response is not None:
                    exc.response.latency_ms = int((time.perf_counter() - t0) * 1000)
                    self._trace.record(
                        req=req,
                        resp=exc.response,
                        provider_name=self._provider_name,
                        stage=stage,
                        attempt=attempt,
                        task_id=task_id,
                        latency_ms=exc.response.latency_ms,
                    )
                raise
            resp.latency_ms = int((time.perf_counter() - t0) * 1000)
            if self._cache is not None and key is not None:
                self._cache.put(key, resp)

        if self._trace is not None:
            self._trace.record(
                req=req,
                resp=resp,
                provider_name=self._provider_name,
                stage=stage,
                attempt=attempt,
                task_id=task_id,
                latency_ms=resp.latency_ms,
            )
        return resp
