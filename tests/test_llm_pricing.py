from pathlib import Path

import pytest

from nepa.config import ConfigError, configured_model_price, load_config, public_config_snapshot
from nepa.llm.client import LLMClient, LLMConfigurationError, LLMRequest, LLMResponse
from nepa.llm.telemetry import calculate_cost


def test_empty_pricing_table_is_persisted_in_snapshot():
    config = load_config(Path("configs/default.yaml"))

    assert config.pricing.models == {}
    assert public_config_snapshot(config)["pricing"] == {"models": {}}


def test_pricing_accepts_non_negative_fixture_rates_and_is_snapshot_stable():
    config = load_config(
        overrides={
            "pricing": {
                "models": {
                    "fixture/model": {
                        "input_usd_per_million_tokens": 0,
                        "output_usd_per_million_tokens": 1.25,
                    }
                }
            }
        }
    )

    assert config.pricing.models["fixture/model"].output_usd_per_million_tokens == 1.25
    assert config.snapshot["pricing"]["models"]["fixture/model"]["input_usd_per_million_tokens"] == 0.0


@pytest.mark.parametrize(
    "override",
    [
        {"pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": -1, "output_usd_per_million_tokens": 0}}}},
        {"pricing": {"models": {"fixture": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0}}}},
        {"pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0, "extra": 1}}}},
    ],
)
def test_invalid_pricing_is_rejected(override):
    with pytest.raises(ConfigError):
        load_config(overrides=override)


def test_missing_price_preflight_fails_without_inventing_a_rate():
    with pytest.raises(ConfigError, match="missing configured price"):
        configured_model_price(load_config(), "fixture", "model")


def _fixture_config():
    return load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {
                "models": {
                    "fixture/model": {
                        "input_usd_per_million_tokens": 2,
                        "output_usd_per_million_tokens": 4,
                    }
                }
            },
        }
    )


class _Provider:
    native_structured_output = False

    def __init__(self, events):
        self.events = events

    def complete(self, request, *, model, native_schema):
        self.events.append("provider")
        return LLMResponse(
            text="ok",
            tokens_in=1_000_000,
            tokens_out=500_000,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": "unknown"},
        )


def test_cost_uses_provider_token_counts_and_immediate_usage_recording_without_stage_usage():
    events = []

    class Orchestrator:
        def admit_external_call(self, store):
            events.append("admit")

        def record_external_usage(self, store, usage):
            events.append(("record", usage.tokens_in, usage.tokens_out, usage.cost_usd))

    client = LLMClient(
        _fixture_config(),
        {"fixture": _Provider(events)},
        orchestrator=Orchestrator(),
        store=object(),
    )
    response = client.complete(
        LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20),
        provider_name="fixture",
        model="model",
    )

    assert response.cost_usd == pytest.approx(4.0)
    assert events == ["admit", "provider", ("record", 1_000_000, 500_000, 4.0)]
    assert not hasattr(response, "usage")


def test_missing_price_preflight_happens_before_provider_or_credential_work():
    calls = []
    config = load_config(overrides={"providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": "MISSING"}}})

    class Provider:
        native_structured_output = False

        def complete(self, *args, **kwargs):
            calls.append("provider")
            raise AssertionError("provider I/O must not start")

    with pytest.raises(LLMConfigurationError, match="missing configured price"):
        LLMClient(config, {"fixture": Provider()}).complete(
            LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20),
            provider_name="fixture",
            model="model",
        )
    assert calls == []


def test_calculate_cost_rejects_negative_counts():
    price = configured_model_price(
        load_config(overrides={"pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}}}),
        "fixture",
        "model",
    )
    with pytest.raises(ValueError):
        calculate_cost(price, -1, 0)
