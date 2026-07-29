"""LLM 请求参数 capability probe（设计文档 8.4）。

证据标准由负责人于 2026-07-28 冻结：
- probe 必须关闭响应缓存；
- 请求被 API 接受只证明语法可接受，不证明参数已应用；
- 只有 provider 显式报告才能写 reported_applied/reported_ignored；
- 没有显式证据或请求失败时保持 unknown。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from nepa.llm.client import (
    LLMError,
    LLMRequest,
    LLMResponse,
    ParameterSupport,
)

__all__ = [
    "CapabilityProbeError",
    "CapabilityProbeResult",
    "ProbeError",
    "probe_parameter_capabilities",
]

EvidenceKind = Literal["provider_report", "request_accepted_only", "no_response"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeError(_StrictModel):
    """未得到 provider 响应时的可审计错误摘要。"""

    type: str
    message: str


class CapabilityProbeResult(_StrictModel):
    """一次 provider/model 参数探测的机器可判结果。"""

    schema_version: Literal["1.0"] = "1.0"
    provider: str
    requested_model: str
    response_model: str | None
    params_requested: dict[str, Any]
    request_accepted: bool
    parameter_support: dict[str, ParameterSupport]
    evidence: dict[str, EvidenceKind]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    error: ProbeError | None


class CapabilityProbeError(RuntimeError):
    """probe 违反内部不变量，而非 provider 能力未知。"""


class ProbeClient(Protocol):
    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
        use_cache: bool = True,
    ) -> LLMResponse: ...


def probe_parameter_capabilities(
    client: ProbeClient,
    *,
    provider: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 16,
    stage: str = "capability_probe",
) -> CapabilityProbeResult:
    """发送一次无结构化修复的最小请求并按冻结证据标准判定。

    该函数不做统计推断。即使请求成功，只要 adapter 没有携带 provider 的
    显式能力报告，对应状态就保持 ``unknown``。
    """
    req = LLMRequest(
        role="capability_probe",
        system=(
            "You are a capability probe. Follow the user instruction exactly and "
            "return no additional explanation."
        ),
        user="Reply with exactly: OK",
        json_schema=None,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    params_requested: dict[str, Any] = {
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        response = client.complete(req, stage=stage, use_cache=False)
    except LLMError as exc:
        support: dict[str, ParameterSupport] = {
            name: "unknown" for name in params_requested
        }
        no_response_evidence: dict[str, EvidenceKind] = {
            name: "no_response" for name in params_requested
        }
        return CapabilityProbeResult(
            provider=provider,
            requested_model=model,
            response_model=None,
            params_requested=params_requested,
            request_accepted=False,
            parameter_support=support,
            evidence=no_response_evidence,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_ms=0,
            error=ProbeError(type=type(exc).__name__, message=str(exc)[:500]),
        )

    if response.cached:
        raise CapabilityProbeError("capability probe returned a cached response")

    parameter_support: dict[str, ParameterSupport] = {}
    evidence: dict[str, EvidenceKind] = {}
    for name in params_requested:
        status = response.parameter_support.get(name, "unknown")
        parameter_support[name] = status
        evidence[name] = (
            "provider_report" if status != "unknown" else "request_accepted_only"
        )

    return CapabilityProbeResult(
        provider=provider,
        requested_model=model,
        response_model=response.model or None,
        params_requested=params_requested,
        request_accepted=True,
        parameter_support=parameter_support,
        evidence=evidence,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
        error=None,
    )
