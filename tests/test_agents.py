"""AgentRunner 的无状态调用与预算计账测试。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from nepa.agents.base import AgentRunner, ClientFactory, TruncatedOutputError
from nepa.agents.contracts import architecture_draft_schema
from nepa.agents.prompt_lint import (
    COMMON_CODE_ROLES,
    lint_non_mqtt_render,
    lint_prompt_directory,
    lint_prompt_source,
)
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
        self.last_request: LLMRequest | None = None
        self.last_trace_extra: dict[str, Any] | None = None

    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
        trace_extra: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        self.last_request = req
        self.last_trace_extra = dict(trace_extra) if trace_extra is not None else None
        del stage, task_id, attempt
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


def test_truncated_output_is_rejected_even_when_schema_valid() -> None:
    """6.4.6：截断输出即使碰巧过 Schema 也不可信，必须直接失败。"""
    response = LLMResponse(
        text='{"ok":true}',
        parsed={"ok": True},
        validation="pass",
        provider_metadata={"finish_reason": "length"},
    )
    usage: list[LLMResponse] = []

    with pytest.raises(TruncatedOutputError) as excinfo:
        _runner(response, usage, fail=False).invoke(
            "coder", {"task": "x"}, _SCHEMA, stage="S5"
        )

    assert excinfo.value.finish_reason == "length"
    assert usage == [response]  # 截断调用同样消耗预算


def test_trace_extra_reaches_the_client() -> None:
    response = LLMResponse(text='{"ok":true}', parsed={"ok": True}, validation="pass")
    client = _Client(response)
    runner = AgentRunner(cast(RoleRegistry, _Registry()), cast(ClientFactory, _Factory(client)))

    runner.invoke(
        "coder",
        {"task": "x"},
        _SCHEMA,
        stage="S4",
        trace_extra={"compiler_phase": "ARCHITECT"},
    )

    assert client.last_trace_extra == {"compiler_phase": "ARCHITECT"}


def test_common_code_prompt_sources_are_protocol_neutral() -> None:
    prompts_dir = Path(__file__).parents[1] / "nepa" / "agents" / "prompts"

    assert lint_prompt_directory(prompts_dir) == []


def test_prompt_source_lint_reports_identifier_location() -> None:
    findings = lint_prompt_source("coder", "first line\ncall mqtt_encode_packet now")

    assert len(findings) == 1
    assert findings[0].value == "mqtt_encode_packet"
    assert findings[0].line == 2
    assert findings[0].column == 6


@pytest.mark.parametrize("role_name", COMMON_CODE_ROLES)
def test_non_mqtt_fixture_render_has_no_mqtt_residue(role_name: str) -> None:
    response = LLMResponse(text='{"ok":true}', parsed={"ok": True})
    runner = _runner(response, [], fail=False)
    payload = {
        "protocol": "sample-wire",
        "task": {
            "deliverable_files": ["src/frame_codec.rs"],
            "required_contracts": ["frame-codec"],
        },
        "language_profile": {
            "language": "Rust",
            "coding_rules": ["Use the standard library only."],
        },
        "interfaces": ["encode_frame(input, output)"],
    }

    rendered = runner.render_prompt(role_name, payload, _SCHEMA)

    assert '"protocol": "sample-wire"' in rendered
    assert lint_non_mqtt_render(role_name, rendered) == []


def test_non_mqtt_render_lint_rejects_protocol_name_path_and_interface() -> None:
    rendered = "protocol MQTT\npath src/mqtt/codec.c\ncall mqtt_encode_packet"

    findings = lint_non_mqtt_render("coder", rendered)

    assert [finding.value.lower() for finding in findings] == [
        "mqtt",
        "mqtt",
        "mqtt_encode_packet",
    ]


def test_architecture_planner_prompt_is_independent_and_schema_is_activity_source() -> None:
    response = LLMResponse(text='{"ok":true}', parsed={"ok": True})
    runner = _runner(response, [], fail=False)
    schema = architecture_draft_schema()
    rendered = runner.render_prompt(
        "architecture_planner",
        {
            "planning_index": {"protocol": {"name": "sample-wire"}},
            "delivery_constraints": {
                "external_contracts": [{"id": "frame-cli"}],
                "file_slots": [{"path": "src/frame.c", "mutability": "s6_owned"}],
            },
        },
        schema,
    )

    assert "ArchitectureDraft" in rendered
    assert "provider_work_package_id" in rendered
    assert schema["$id"] == "https://nepa.dev/schemas/architecture-draft.schema.json"
    assert "planner_input" not in rendered
