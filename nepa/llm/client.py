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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

import httpx
import jsonschema
from pydantic import BaseModel, Field

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "ParameterSupport",
    "Provider",
    "ProviderCallRecord",
    "ProviderHTTPError",
    "ProviderResponseError",
    "RawResult",
    "RetryExhaustedError",
    "StructuredOutputError",
    "StructuredProvider",
    "extract_first_json",
    "request_with_retries",
    "schema_errors",
]

ParameterSupport: TypeAlias = Literal[
    "reported_applied",
    "reported_ignored",
    "unknown",
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


class ProviderResponseError(LLMError):
    """Provider returned HTTP success with a malformed or error-shaped body."""


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
    tier: str | None = None  # 实际解析档位；只用于 trace/成本归因
    system: str = ""
    user: str
    json_schema: dict[str, Any] | None = None  # 非空则要求结构化输出
    temperature: float = 0.0
    max_tokens: int = 4096


class ProviderCallRecord(BaseModel):
    """一次真实 provider 调用的完整运行时证据。

    StructuredProvider 可能为一个逻辑 structured completion 发起初次调用和
    一次格式修复调用。两次必须分别落 trace；本记录仅在内存中传给 LLMClient，
    不序列化进响应缓存。
    """

    request: LLMRequest
    text: str
    parsed: dict[str, Any] | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    parameter_support: dict[str, ParameterSupport] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    validation: Literal["pass", "repaired", "fail"] | None = None
    latency_ms: int = 0
    provider_call_index: int
    call_kind: Literal["initial", "format_repair"]

    def as_response(self) -> LLMResponse:
        """构造供 TraceWriter 使用的单次调用响应视图。"""
        return LLMResponse(
            text=self.text,
            parsed=self.parsed,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            model=self.model,
            parameter_support=dict(self.parameter_support),
            provider_metadata=dict(self.provider_metadata),
            validation=self.validation,
            latency_ms=self.latency_ms,
        )


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
    parameter_support: dict[str, ParameterSupport] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    validation: Literal["pass", "repaired", "fail"] | None = None  # 5.5
    latency_ms: int = 0
    provider_calls: list[ProviderCallRecord] = Field(default_factory=list, exclude=True)


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
    parameter_support: dict[str, ParameterSupport]
    provider_metadata: dict[str, Any]


class StructuredProvider(ABC):
    """统一结构化输出策略基类：行为对所有 provider 一致（8.4 要点 2）。

    子类只需实现 _raw_complete（一次 HTTP 补全，含自身的重试逻辑）。
    """

    name: str = "provider"
    model: str = ""
    supports_native_json: bool = False  # True 时 _raw_complete 收到 json_mode=True

    @staticmethod
    def _response_fields(raw: RawResult) -> dict[str, Any]:
        """把 provider 明确报告的能力与元数据原样提升到统一响应。

        adapter 不得从 HTTP 成功推断采样参数已应用；不能证明时 provider
        必须在 RawResult 中写 ``unknown``（8.4 要点 4）。
        """
        return {
            "parameter_support": dict(raw.parameter_support),
            "provider_metadata": dict(raw.provider_metadata),
        }

    @staticmethod
    def _merge_parameter_support(
        *results: RawResult,
    ) -> dict[str, ParameterSupport]:
        """合并结构修复涉及的多次调用；报告不一致时保守降为 unknown。"""
        keys = {key for result in results for key in result.parameter_support}
        merged: dict[str, ParameterSupport] = {}
        for key in sorted(keys):
            values = {result.parameter_support.get(key, "unknown") for result in results}
            merged[key] = values.pop() if len(values) == 1 else "unknown"
        return merged

    @classmethod
    def _repair_response_fields(cls, *results: RawResult) -> dict[str, Any]:
        """保留结构修复每次 provider 调用的元数据，避免聚合后丢失证据。"""
        return {
            "parameter_support": cls._merge_parameter_support(*results),
            "provider_metadata": {
                "calls": [dict(result.provider_metadata) for result in results],
                "finish_reason": results[-1].provider_metadata.get("finish_reason"),
            },
        }

    @abstractmethod
    def _raw_complete(
        self, *, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool
    ) -> RawResult: ...

    def _invoke_raw(
        self,
        req: LLMRequest,
        *,
        user: str,
        json_mode: bool,
    ) -> tuple[RawResult, int]:
        """执行并计时一次真实 provider 调用（含该调用自己的基础设施重试）。"""
        started = time.perf_counter()
        raw = self._raw_complete(
            system=req.system,
            user=user,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            json_mode=json_mode,
        )
        return raw, int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _call_record(
        req: LLMRequest,
        raw: RawResult,
        *,
        user: str,
        parsed: dict[str, Any] | None,
        validation: Literal["pass", "repaired", "fail"] | None,
        latency_ms: int,
        provider_call_index: int,
        call_kind: Literal["initial", "format_repair"],
    ) -> ProviderCallRecord:
        """把一次 raw 调用规范化为可逐行落 trace 的运行时记录。"""
        return ProviderCallRecord(
            request=req.model_copy(update={"user": user}),
            text=raw.text,
            parsed=parsed,
            tokens_in=raw.tokens_in,
            tokens_out=raw.tokens_out,
            model=raw.model,
            parameter_support=dict(raw.parameter_support),
            provider_metadata=dict(raw.provider_metadata),
            validation=validation,
            latency_ms=latency_ms,
            provider_call_index=provider_call_index,
            call_kind=call_kind,
        )

    def complete(self, req: LLMRequest) -> LLMResponse:
        if req.json_schema is None:
            raw, raw_latency_ms = self._invoke_raw(req, user=req.user, json_mode=False)
            return LLMResponse(
                text=raw.text,
                parsed=None,
                tokens_in=raw.tokens_in,
                tokens_out=raw.tokens_out,
                model=raw.model,
                validation=None,
                provider_calls=[
                    self._call_record(
                        req,
                        raw,
                        user=req.user,
                        parsed=None,
                        validation=None,
                        latency_ms=raw_latency_ms,
                        provider_call_index=1,
                        call_kind="initial",
                    )
                ],
                **self._response_fields(raw),
            )

        # 优先原生 JSON 模式；schema 始终内嵌提示词以传达目标结构（8.4 要点 2）
        json_mode = self.supports_native_json
        user = embed_schema_prompt(req.user, req.json_schema)
        raw, raw_latency_ms = self._invoke_raw(req, user=user, json_mode=json_mode)
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
                provider_calls=[
                    self._call_record(
                        req,
                        raw,
                        user=user,
                        parsed=parsed,
                        validation="pass",
                        latency_ms=raw_latency_ms,
                        provider_call_index=1,
                        call_kind="initial",
                    )
                ],
                **self._response_fields(raw),
            )

        # 校验失败：自动发一次修复调用（把错误清单馈给模型）
        repair_user = build_repair_prompt(raw.text, errors, req.json_schema)
        raw2, raw2_latency_ms = self._invoke_raw(req, user=repair_user, json_mode=json_mode)
        tokens_in += raw2.tokens_in
        tokens_out += raw2.tokens_out
        parsed2, errors2 = self._try_parse(raw2.text, req.json_schema)
        first_call = self._call_record(
            req,
            raw,
            user=user,
            parsed=None,
            validation="fail",
            latency_ms=raw_latency_ms,
            provider_call_index=1,
            call_kind="initial",
        )
        if not errors2:
            return LLMResponse(
                text=raw2.text,
                parsed=parsed2,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                model=raw2.model,
                validation="repaired",
                provider_calls=[
                    first_call,
                    self._call_record(
                        req,
                        raw2,
                        user=repair_user,
                        parsed=parsed2,
                        validation="repaired",
                        latency_ms=raw2_latency_ms,
                        provider_call_index=2,
                        call_kind="format_repair",
                    ),
                ],
                **self._repair_response_fields(raw, raw2),
            )

        # 仍失败：向上报错，携带 fail 响应供 trace 落盘（5.5）
        fail_resp = LLMResponse(
            text=raw2.text,
            parsed=None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=raw2.model,
            validation="fail",
            provider_calls=[
                first_call,
                self._call_record(
                    req,
                    raw2,
                    user=repair_user,
                    parsed=None,
                    validation="fail",
                    latency_ms=raw2_latency_ms,
                    provider_call_index=2,
                    call_kind="format_repair",
                ),
            ],
            **self._repair_response_fields(raw, raw2),
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
        provider_call_index: int | None = None,
        call_kind: str = "initial",
        extra: Mapping[str, Any] | None = None,
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

    def _record_trace(
        self,
        req: LLMRequest,
        resp: LLMResponse,
        *,
        stage: str,
        task_id: str | None,
        attempt: int,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """逐真实 provider 调用落 trace，并把单次成本汇总回逻辑响应。"""
        if self._trace is None:
            return

        if not resp.cached and resp.provider_calls:
            total_cost = 0.0
            for call in resp.provider_calls:
                call_resp = call.as_response()
                line = self._trace.record(
                    req=call.request,
                    resp=call_resp,
                    provider_name=self._provider_name,
                    stage=stage,
                    attempt=attempt,
                    task_id=task_id,
                    latency_ms=call.latency_ms,
                    provider_call_index=call.provider_call_index,
                    call_kind=call.call_kind,
                    extra=extra,
                )
                total_cost += float(line.get("cost_usd", 0.0))
            resp.cost_usd = round(total_cost, 8)
            return

        # 缓存命中没有真实 provider 调用，但保留一条零成本 replay 事件，
        # 明确与 initial/format_repair 区分，便于重放审计。
        self._trace.record(
            req=req,
            resp=resp,
            provider_name=self._provider_name,
            stage=stage,
            attempt=attempt,
            task_id=task_id,
            latency_ms=resp.latency_ms,
            provider_call_index=None,
            call_kind="cache_replay" if resp.cached else "initial",
            extra=extra,
        )

    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
        use_cache: bool = True,
        trace_extra: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        key: str | None = None
        resp: LLMResponse | None = None
        if use_cache and self._cache is not None:
            key = self._cache.make_key(self._provider_name, self._model, req)
            resp = self._cache.get(key)

        if resp is None:
            t0 = time.perf_counter()
            try:
                resp = self._provider.complete(req)
            except StructuredOutputError as exc:
                # 两次均失败时仍逐 raw call 落 trace，再向上抛。
                if exc.response is not None:
                    exc.response.latency_ms = int((time.perf_counter() - t0) * 1000)
                    self._record_trace(
                        req,
                        exc.response,
                        stage=stage,
                        task_id=task_id,
                        attempt=attempt,
                        extra=trace_extra,
                    )
                raise
            resp.latency_ms = int((time.perf_counter() - t0) * 1000)
            if use_cache and self._cache is not None and key is not None:
                self._cache.put(key, resp)

        self._record_trace(
            req,
            resp,
            stage=stage,
            task_id=task_id,
            attempt=attempt,
            extra=trace_extra,
        )
        return resp
