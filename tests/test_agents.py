"""AgentRunner 的无状态调用与预算计账测试。"""

from __future__ import annotations

from typing import Any, cast

import pytest

from nepa.agents.base import AgentRunner, ClientFactory
from nepa.agents.roles import ResolvedRole, RoleRegistry
from nepa.llm.client import LLMRequest, LLMResponse, StructuredOutputError


class _Registry:
    def resolve(self, role_name: str, *, tier_override: str | None = None) -> ResolvedRole:
        del tier_override
        return ResolvedRole(
            name=role_name,
            tier="strong",
            provider="stub",
            model="stub-model",
            temperature=0.0,
            max_tokens=128,
            escalate_to=None,
        )


class _Client:
    def __init__(self, response: LLMResponse, *, fail: bool = False) -> None:
        self.response = response
        self.fail = fail

    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
    ) -> LLMResponse:
        del req, stage, task_id, attempt
        if self.fail:
            raise StructuredOutputError(["invalid output"], self.response)
        return self.response


class _Factory:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def client_for(self, role: ResolvedRole) -> _Client:
        del role
        return self.client


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _runner(response: LLMResponse, usage: list[LLMResponse], *, fail: bool) -> AgentRunner:
    return AgentRunner(
        cast(RoleRegistry, _Registry()),
        cast(ClientFactory, _Factory(_Client(response, fail=fail))),
        on_usage=usage.append,
    )


def test_successful_invocation_counts_usage_once() -> None:
    response = LLMResponse(
        text='{"ok":true}',
        parsed={"ok": True},
        tokens_in=11,
        tokens_out=3,
        validation="pass",
    )
    usage: list[LLMResponse] = []

    parsed = _runner(response, usage, fail=False).invoke(
        "coder", {"task": "x"}, _SCHEMA, stage="S5", task_id="T1"
    )

    assert parsed == {"ok": True}
    assert usage == [response]


def test_failed_structured_invocation_still_counts_usage_once() -> None:
    response = LLMResponse(
        text="still invalid",
        tokens_in=23,
        tokens_out=7,
        validation="fail",
    )
    usage: list[LLMResponse] = []

    with pytest.raises(StructuredOutputError):
        _runner(response, usage, fail=True).invoke(
            "coder", {"task": "x"}, _SCHEMA, stage="S5", task_id="T1"
        )

    assert usage == [response]
