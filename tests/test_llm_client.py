import pytest
from pydantic import ValidationError

from nepa.config import load_config
from nepa.llm.client import (
    LLMCallContext,
    LLMClient,
    LLMConfigurationError,
    LLMRequest,
    LLMRequestError,
    LLMResponse,
    ParameterSupportState,
)


def test_contract_models_reject_extra_fields_and_keep_stage_codes_out():
    request = LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=32)
    response = LLMResponse(
        text="ok",
        tokens_in=1,
        tokens_out=1,
        cost_usd=0,
        model="fixture/model",
        parameter_support={"temperature": ParameterSupportState.UNKNOWN},
    )

    assert request.role == "fixture"
    assert response.parameter_support["temperature"] == ParameterSupportState.UNKNOWN
    assert not hasattr(response, "stage_code")
    with pytest.raises(ValidationError):
        LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=0)
    with pytest.raises(ValidationError):
        LLMResponse(
            text="ok",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0,
            model="fixture/model",
            parameter_support={},
            stage_code="S6",
        )


def test_invalid_request_and_target_fail_before_provider_io():
    client = LLMClient(load_config())
    request = LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=32)

    assert client.validate_request(request) is request
    with pytest.raises(LLMRequestError):
        client.validate_request(object())
    with pytest.raises(LLMConfigurationError):
        client.validate_target("missing", "fixture-model")
    with pytest.raises(LLMConfigurationError):
        client.validate_target("qwen", "")


def test_invalid_template_provenance_fails_before_cache_or_provider_work():
    calls = []

    class Provider:
        native_structured_output = False

        def complete(self, *args, **kwargs):
            calls.append("provider")
            raise AssertionError("provider must not be called")

    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}},
        }
    )
    client = LLMClient(config, {"fixture": Provider()})
    client.cache = type("Cache", (), {"load": lambda self, key: (_ for _ in ()).throw(AssertionError("cache must not be read"))})()
    context = LLMCallContext(
        run_id="run",
        stage="S4",
        tier="T1",
        trace_fields={"prompt_template_sha256": "A" * 64},
    )
    with pytest.raises(LLMRequestError, match="lowercase 64-character"):
        client.complete(
            LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20),
            provider_name="fixture",
            model="model",
            context=context,
        )
    assert calls == []


def test_omitted_template_provenance_keeps_existing_call_contract():
    class Provider:
        native_structured_output = False

        def complete(self, request, *, model, native_schema):
            return LLMResponse(
                text="ok",
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
                model=model,
                parameter_support={"temperature": ParameterSupportState.UNKNOWN},
            )

    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1}}},
        }
    )
    response = LLMClient(config, {"fixture": Provider()}).complete(
        LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20),
        provider_name="fixture",
        model="model",
    )
    assert response.text == "ok"
