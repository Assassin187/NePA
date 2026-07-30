"""无状态 Agent 调用器（设计文档 4.2 L3、4.5、8.8）。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from nepa.agents.roles import ResolvedRole, RoleRegistry
from nepa.llm.client import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    StructuredOutputError,
)


class ClientFactory(Protocol):
    def client_for(self, role: ResolvedRole) -> LLMClient: ...


class TruncatedOutputError(RuntimeError):
    """provider 报告输出被截断（5.5 finish_reason，6.4.6 直接失败）。

    截断的结构化输出即使碰巧通过 Schema 也不可信，不允许当作正常候选继续。
    """

    def __init__(self, role: str, finish_reason: str) -> None:
        super().__init__(f"{role} 输出被截断: finish_reason={finish_reason}")
        self.role = role
        self.finish_reason = finish_reason


# provider 报告的截断类 finish_reason（8.4 两个内置 provider 的取值）。
_TRUNCATION_REASONS: frozenset[str] = frozenset({"length", "max_tokens", "model_length"})


class AgentRunner:
    """模板渲染 → 静态路由 → LLM 结构化调用 → 预算回调。"""

    def __init__(
        self,
        registry: RoleRegistry,
        client_factory: ClientFactory,
        *,
        prompts_dir: str | Path | None = None,
        on_usage: Callable[[LLMResponse], None] | None = None,
    ) -> None:
        self.registry = registry
        self.client_factory = client_factory
        directory = (
            Path(prompts_dir)
            if prompts_dir is not None
            else Path(__file__).resolve().parent / "prompts"
        )
        self._env = Environment(
            loader=FileSystemLoader(directory),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self._on_usage = on_usage

    def render_prompt(
        self,
        role_name: str,
        payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> str:
        """渲染一次无状态 user prompt，供调用与协议中立审计共用。"""
        template = self._env.get_template(f"{role_name}.md")
        return template.render(
            payload_json=json.dumps(payload, ensure_ascii=False, indent=2),
            output_schema_json=json.dumps(output_schema, ensure_ascii=False, indent=2),
        )

    def invoke(
        self,
        role_name: str,
        payload: dict[str, Any],
        output_schema: dict[str, Any],
        *,
        stage: str,
        attempt: int = 1,
        task_id: str | None = None,
        tier_override: str | None = None,
        trace_extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """一次调用无会话历史；输入只由显式 payload 构成（P5）。

        ``trace_extra`` 透传给 trace 行，供 S4 记录 ``compiler_phase`` 等阶段
        证据（5.5）。截断输出直接抛 ``TruncatedOutputError``（6.4.6）。
        """
        role = self.registry.resolve(role_name, tier_override=tier_override)
        user = self.render_prompt(role_name, payload, output_schema)
        req = LLMRequest(
            role=role_name,
            tier=role.tier,
            system=(
                "You are a stateless NePA agent. Use only the explicitly delimited input. "
                "Do not rely on protocol knowledge that is absent from the input."
            ),
            user=user,
            json_schema=output_schema,
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )
        try:
            response = self.client_factory.client_for(role).complete(
                req,
                stage=stage,
                task_id=task_id,
                attempt=attempt,
                trace_extra=trace_extra,
            )
        except StructuredOutputError as exc:
            # 结构化修复仍失败时 response 已包含两次模型调用的合计 token；
            # 失败 attempt 同样消耗运行预算，不能只依赖成功返回路径计账。
            if self._on_usage is not None and exc.response is not None:
                self._on_usage(exc.response)
            raise
        if self._on_usage is not None:
            self._on_usage(response)
        finish_reason = str(response.provider_metadata.get("finish_reason") or "")
        if finish_reason in _TRUNCATION_REASONS:
            raise TruncatedOutputError(role_name, finish_reason)
        if response.parsed is None:
            raise RuntimeError(f"{role_name} did not return a parsed structured response")
        return response.parsed
