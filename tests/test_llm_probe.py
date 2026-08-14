import json

from nepa.config import load_config
from nepa.llm.client import LLMClient, LLMRequest, LLMResponse, ParameterSupportState, ProviderError
from nepa.llm.telemetry import LLMTelemetry
from nepa.run_store import RunStore


def _config():
    return load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {
                "models": {
                    "fixture/model": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0},
                    "fixture/model-v1": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0},
                    "fixture/model-v2": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0},
                }
            },
        }
    )


class _Fake:
    native_structured_output = False

    def __init__(self, text, support):
        self.text = text
        self.support = support

    def complete(self, request, *, model, native_schema):
        return LLMResponse(
            text=self.text,
            tokens_in=1,
            tokens_out=1,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": self.support},
        )


def _request(temperature):
    return LLMRequest(role="fixture", system="system", user=f"user-{temperature}", temperature=temperature, max_tokens=20)


def test_unknown_is_preserved_without_provider_report():
    response = LLMClient(_config(), {"fixture": _Fake("same", ParameterSupportState.UNKNOWN)}).complete(
        _request(0), provider_name="fixture", model="model"
    )
    assert response.parameter_support["temperature"] is ParameterSupportState.UNKNOWN


def test_no_inference_from_output_difference_or_model_name():
    first = LLMClient(_config(), {"fixture": _Fake("first", "unknown")}).complete(
        _request(0), provider_name="fixture", model="model-v1"
    )
    second = LLMClient(_config(), {"fixture": _Fake("second", "unknown")}).complete(
        _request(1), provider_name="fixture", model="model-v2"
    )

    assert first.text != second.text
    assert first.parameter_support["temperature"] is ParameterSupportState.UNKNOWN
    assert second.parameter_support["temperature"] is ParameterSupportState.UNKNOWN


def test_provider_report_is_the_only_positive_capability_evidence():
    applied = LLMClient(_config(), {"fixture": _Fake("reported", "reported_applied")}).complete(
        _request(0.2), provider_name="fixture", model="model"
    )
    ignored = LLMClient(_config(), {"fixture": _Fake("reported", "reported_ignored")}).complete(
        _request(0.2), provider_name="fixture", model="model"
    )

    assert applied.parameter_support["temperature"] is ParameterSupportState.REPORTED_APPLIED
    assert ignored.parameter_support["temperature"] is ParameterSupportState.REPORTED_IGNORED


def test_probe_accepted_only_is_unknown_and_bypasses_cache(tmp_path):
    calls = []

    class Provider(_Fake):
        def complete(self, request, *, model, native_schema):
            calls.append(request)
            return super().complete(request, model=model, native_schema=native_schema)

    from nepa.run_store import RunStore

    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    client = LLMClient(_config(), {"fixture": Provider("OK", "unknown")}, store=store)
    result = client.probe_parameter(provider_name="fixture", model="model", requested_value=0.4)

    assert result.accepted is True
    assert result.state is ParameterSupportState.UNKNOWN
    assert result.evidence_kind == "request_accepted_only"
    assert result.returned_model == "model"
    assert result.cost_usd == 0
    assert len(calls) == 1
    assert list((store.root / "cache").rglob("*.json")) == [] if (store.root / "cache").exists() else True
    assert client.probe_records == (result,)
    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])
    probe = row["capability_probe"]
    assert probe["provider"] == "fixture"
    assert probe["model"] == "model"
    assert probe["parameter"] == "temperature"
    assert probe["requested_value"] == 0.4
    assert probe["accepted"] is True
    assert probe["returned_model"] == "model"
    assert probe["tokens_in"] == 1
    assert probe["tokens_out"] == 1
    assert probe["cost_usd"] == 0.0
    assert probe["error"] is None
    assert probe["state"] == "unknown"
    assert probe["evidence_kind"] == "request_accepted_only"


def test_probe_explicit_provider_report_is_preserved():
    result = LLMClient(_config(), {"fixture": _Fake("OK", "reported_applied")}).probe_parameter(
        provider_name="fixture", model="model", requested_value=0.2
    )

    assert result.accepted is True
    assert result.state is ParameterSupportState.REPORTED_APPLIED
    assert result.evidence_kind == "provider_report"


def test_probe_failure_is_unknown_and_keeps_sanitized_error():
    class Failed:
        native_structured_output = False

        def complete(self, request, *, model, native_schema):
            raise ProviderError("provider failure without credential", provider="fixture", status_code=400)

    result = LLMClient(_config(), {"fixture": Failed()}).probe_parameter(
        provider_name="fixture", model="model", requested_value=0.2
    )

    assert result.accepted is False
    assert result.state is ParameterSupportState.UNKNOWN
    assert result.evidence_kind == "none"
    assert "provider failure" in result.error


def test_probe_failure_persists_complete_evidence(tmp_path):
    class Failed:
        native_structured_output = False

        def complete(self, request, *, model, native_schema):
            raise ProviderError("provider failure", provider="fixture", status_code=400)

    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    result = LLMClient(
        _config(),
        {"fixture": Failed()},
        store=store,
        telemetry=LLMTelemetry(store),
    ).probe_parameter(provider_name="fixture", model="model", requested_value=0.2)

    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])
    probe = row["capability_probe"]
    assert result.accepted is False
    assert probe["provider"] == "fixture"
    assert probe["model"] == "model"
    assert probe["parameter"] == "temperature"
    assert probe["requested_value"] == 0.2
    assert probe["accepted"] is False
    assert probe["returned_model"] is None
    assert probe["tokens_in"] == 0
    assert probe["tokens_out"] == 0
    assert probe["cost_usd"] == 0
    assert probe["error"] == "provider failure"
    assert probe["state"] == "unknown"
    assert probe["evidence_kind"] == "none"
