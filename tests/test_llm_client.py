import pytest
from pydantic import ValidationError

from nepa.config import load_config
from nepa.llm.client import (
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
