import json

from nepa.config import load_config
import pytest

from nepa.llm.client import BudgetExhausted, LLMClient, LLMRequest, LLMResponse
from nepa.llm.telemetry import LLMTelemetry
from nepa.run_store import RunStore


class _Provider:
    native_structured_output = False

    def complete(self, request, *, model, native_schema):
        return LLMResponse(
            text="ok",
            tokens_in=10,
            tokens_out=5,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": "unknown"},
        )


def _client(events):
    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )

    class Orchestrator:
        def admit_external_call(self, store):
            events.append("admit")

        def record_external_usage(self, store, usage):
            events.append(("record", usage.tokens_in, usage.tokens_out, usage.cost_usd))

    return LLMClient(config, {"fixture": _Provider()}, orchestrator=Orchestrator(), store=object())


def test_immediate_usage_is_recorded_before_client_returns():
    events = []
    response = _client(events).complete(
        LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20),
        provider_name="fixture",
        model="model",
    )

    assert response.cost_usd == 20 / 1_000_000
    assert events == ["admit", ("record", 10, 5, 20 / 1_000_000)]


def test_no_double_charge_stage_result_usage_path_exists():
    response = _client([]).complete(
        LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20),
        provider_name="fixture",
        model="model",
    )

    assert not hasattr(response, "usage")


def test_budget_exhausted_after_response_retains_evidence_and_suppresses_repair():
    calls = []

    class StructuredProvider(_Provider):
        def complete(self, request, *, model, native_schema):
            calls.append(request)
            return LLMResponse(
                text='{"invalid": true}',
                tokens_in=10,
                tokens_out=5,
                cost_usd=0,
                model=model,
                parameter_support={"temperature": "unknown"},
            )

    class ExhaustingOrchestrator:
        def admit_external_call(self, store):
            pass

        def record_external_usage(self, store, usage):
            raise BudgetExhausted("budget crossed after response")

    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )
    schema = {"type": "object", "required": ["answer"]}
    client = LLMClient(
        config,
        {"fixture": StructuredProvider()},
        orchestrator=ExhaustingOrchestrator(),
        store=object(),
    )

    with pytest.raises(BudgetExhausted) as exc_info:
        client.complete(
            LLMRequest(role="fixture", system="system", user="user", json_schema=schema, temperature=0, max_tokens=20),
            provider_name="fixture",
            model="model",
        )

    assert len(calls) == 1
    assert exc_info.value.completed_response.validation.value == "fail"
    assert len(client.pending_evidence) == 1
    assert client.pending_evidence[0].cost_usd == pytest.approx(20 / 1_000_000)
    assert client.take_pending_evidence()[0].text == '{"invalid": true}'
    assert client.pending_evidence == ()


def test_budget_exhausted_after_repair_aggregates_all_trace_usage(tmp_path):
    class StructuredProvider:
        native_structured_output = False

        def __init__(self):
            self.calls = 0

        def complete(self, request, *, model, native_schema):
            self.calls += 1
            return LLMResponse(
                text='{"answer":3}' if self.calls == 1 else '{"answer":4}',
                tokens_in=10,
                tokens_out=5,
                cost_usd=0,
                model=model,
                parameter_support={"temperature": "unknown"},
            )

    class ExhaustingOrchestrator:
        def __init__(self):
            self.records = 0

        def admit_external_call(self, store):
            pass

        def record_external_usage(self, store, usage):
            self.records += 1
            if self.records == 2:
                raise BudgetExhausted("budget crossed after repair response")

    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    client = LLMClient(
        config,
        {"fixture": StructuredProvider()},
        orchestrator=ExhaustingOrchestrator(),
        store=store,
        telemetry=LLMTelemetry(store),
    )
    with pytest.raises(BudgetExhausted):
        client.complete(
            LLMRequest(
                role="fixture",
                system="system",
                user="user",
                json_schema={
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string"}},
                },
                temperature=0,
                max_tokens=20,
            ),
            provider_name="fixture",
            model="model",
        )

    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])
    assert row["tokens_in"] == 20
    assert row["tokens_out"] == 10
    assert row["cost_usd"] == pytest.approx(40 / 1_000_000)
    assert len(row["provider_output_paths"]) == 2
