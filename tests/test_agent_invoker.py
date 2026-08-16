import hashlib
from importlib import resources
import json

import pytest

from nepa.agents.base import (
    AGENT_SYSTEM_INSTRUCTION,
    AgentInvoker,
    AgentRequestError,
)
from nepa.config import load_config
from nepa.llm.client import (
    LLMClient,
    LLMResponse,
    ParameterSupportState,
    ProviderError,
)
from nepa.llm.telemetry import LLMTelemetry
from nepa.run_store import RunStore


SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
EXAMPLE = {"answer": "ok"}
INPUTS = {"planning_index": {"name": "OrbitNet"}, "delivery_constraints": {"limit": 3}}


def _config():
    return load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/fixture-model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}},
            "roles": {"architecture_planner": {"tier": "T2", "provider": "fixture", "model": "fixture-model"}},
        }
    )


class _Provider:
    native_structured_output = False

    def __init__(self, *, text='{"answer":"ok"}', failure=None):
        self.calls = []
        self.text = text
        self.failure = failure

    def complete(self, request, *, model, native_schema):
        self.calls.append((request, model, native_schema))
        if self.failure is not None:
            raise self.failure
        return LLMResponse(
            text=self.text,
            tokens_in=2,
            tokens_out=3,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


def test_invoker_constructs_one_request_and_preserves_trace_identity():
    provider = _Provider()
    client = LLMClient(_config(), {"fixture": provider})
    result = AgentInvoker(_config(), client).invoke(
        role="architecture_planner",
        inputs=INPUTS,
        output_schema=SCHEMA,
        output_example=EXAMPLE,
        run_id="caller-run",
        stage="S4",
        task_id="task-1",
        attempt=2,
        use_cache=False,
    )
    assert len(provider.calls) == 1
    request, model, native_schema = provider.calls[0]
    assert request.system == AGENT_SYSTEM_INSTRUCTION
    assert request.user.count("## Role and Goal") == 1
    assert request.json_schema == SCHEMA
    assert model == "fixture-model"
    assert native_schema is False
    assert result.response.parsed == {"answer": "ok"}
    assert result.parsed == {"answer": "ok"}
    assert result.response.model == "fixture-model"
    assert result.route.provider == "fixture"
    assert len(result.raw_template_sha256) == 64


class _RecordingClient:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def complete(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text='{"answer":"ok"}',
            parsed={"answer": "ok"},
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            model=kwargs["model"],
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


@pytest.mark.parametrize(
    ("run_id", "stage", "attempt"),
    [("", "S4", 1), ("run", "S6", 1), ("run", "S4", 0)],
)
def test_invalid_identity_or_stage_fails_before_client_call(run_id, stage, attempt):
    client = _RecordingClient()
    with pytest.raises(AgentRequestError):
        AgentInvoker(_config(), client).invoke(
            role="architecture_planner",
            inputs=INPUTS,
            output_schema=SCHEMA,
            output_example=EXAMPLE,
            run_id=run_id,
            stage=stage,
            attempt=attempt,
        )
    assert client.calls == []


def test_typed_llm_failure_is_propagated_without_agent_retry_or_escalation():
    error = ProviderError("provider failed", provider="fixture", status_code=400)
    client = _RecordingClient(error)
    with pytest.raises(ProviderError) as caught:
        AgentInvoker(_config(), client).invoke(
            role="architecture_planner",
            inputs=INPUTS,
            output_schema=SCHEMA,
            output_example=EXAMPLE,
            run_id="run",
            stage="S4",
        )
    assert caught.value is error
    assert len(client.calls) == 1


def test_real_client_repair_remains_inside_one_agent_logical_call():
    class RepairProvider(_Provider):
        def __init__(self):
            super().__init__()
            self.responses = ['{"answer":3}', '{"answer":"fixed"}']

        def complete(self, request, *, model, native_schema):
            self.calls.append((request, model, native_schema))
            return LLMResponse(
                text=self.responses.pop(0),
                tokens_in=1,
                tokens_out=1,
                cost_usd=0,
                model=model,
                parameter_support={"temperature": ParameterSupportState.UNKNOWN},
            )

    provider = RepairProvider()
    client = LLMClient(_config(), {"fixture": provider})
    result = AgentInvoker(_config(), client).invoke(
        role="architecture_planner",
        inputs=INPUTS,
        output_schema=SCHEMA,
        output_example=EXAMPLE,
        run_id="run",
        stage="S4",
        use_cache=False,
    )
    assert result.parsed == {"answer": "fixed"}
    assert len(provider.calls) == 2


def test_agent_template_hash_is_distinct_from_effective_hash_and_persists_on_cache_hit(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    provider = _Provider()
    client = LLMClient(_config(), {"fixture": provider}, store=store, telemetry=LLMTelemetry(store))
    invoker = AgentInvoker(_config(), client)
    first = invoker.invoke(
        role="architecture_planner",
        inputs=INPUTS,
        output_schema=SCHEMA,
        output_example=EXAMPLE,
        run_id="run",
        stage="S4",
        use_cache=True,
    )
    second = invoker.invoke(
        role="architecture_planner",
        inputs=INPUTS,
        output_schema=SCHEMA,
        output_example=EXAMPLE,
        run_id="run",
        stage="S4",
        use_cache=True,
    )
    rows = [json.loads(line) for line in (store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()]
    assert len(provider.calls) == 1
    assert second.response.cached is True
    assert first.raw_template_sha256 != first.effective_prompt_sha256
    assert first.raw_template_sha256 == hashlib.sha256(
        resources.files("nepa.agents.prompts").joinpath(first.template_path).read_bytes()
    ).hexdigest()
    assert [row["prompt_template_sha256"] for row in rows] == [first.raw_template_sha256] * 2
    assert all(row["prompt_sha256"] != row["prompt_template_sha256"] for row in rows)
