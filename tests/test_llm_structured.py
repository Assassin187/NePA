import pytest

from nepa.config import load_config
from nepa.llm.client import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ParameterSupportState,
    StructuredOutputError,
    extract_first_json_value,
    structured_validation_errors,
)


SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}, "additionalProperties": False}


def _config():
    return load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0}}},
        }
    )


def _request():
    return LLMRequest(role="fixture", system="system", user="user", json_schema=SCHEMA, temperature=0, max_tokens=32)


class _Provider:
    def __init__(self, text, *, native=False, parsed=None):
        self.text = text
        self.native_structured_output = native
        self.parsed = parsed
        self.requests = []

    def complete(self, request, *, model, native_schema):
        self.requests.append((request, native_schema))
        return LLMResponse(
            text=self.text,
            parsed=self.parsed,
            tokens_in=1,
            tokens_out=2,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


class _RepairProvider(_Provider):
    def __init__(self, texts):
        super().__init__(texts[0])
        self.texts = list(texts)

    def complete(self, request, *, model, native_schema):
        self.requests.append((request, native_schema))
        text = self.texts.pop(0)
        return LLMResponse(
            text=text,
            tokens_in=10,
            tokens_out=5,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


def test_native_structured_output_validates_and_returns_parsed_value():
    provider = _Provider('{"answer":"native"}', native=True)
    response = LLMClient(_config(), {"fixture": provider}).complete(
        _request(), provider_name="fixture", model="model"
    )

    assert response.parsed == {"answer": "native"}
    assert provider.requests[0][1] is True
    assert "JSON Schema" not in provider.requests[0][0].user


def test_fallback_embeds_schema_and_extracts_nested_json_from_prose():
    provider = _Provider('prose before {"answer":"fallback", "nested":{"items":[1,2]}} prose after')
    schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
    request = _request().model_copy(update={"json_schema": schema})
    response = LLMClient(_config(), {"fixture": provider}).complete(
        request, provider_name="fixture", model="model"
    )

    assert response.parsed == {"answer": "fallback", "nested": {"items": [1, 2]}}
    assert provider.requests[0][1] is False
    assert '"required"' in provider.requests[0][0].user


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('prefix [1, {"x": 2}] suffix', [1, {"x": 2}]),
        ('prefix {"x":{"y":[true,false]}} suffix', {"x": {"y": [True, False]}}),
    ],
)
def test_first_json_extractor_handles_arrays_and_nested_values(text, expected):
    assert extract_first_json_value(text) == expected


def test_malformed_text_and_schema_violations_return_deterministic_errors():
    with pytest.raises(StructuredOutputError, match="no complete JSON"):
        extract_first_json_value("plain prose only")
    errors = structured_validation_errors(SCHEMA, {"answer": 3, "extra": True})
    assert errors == [
        {"path": "$", "code": "additionalProperties", "message": "Additional properties are not allowed ('extra' was unexpected)"},
        {"path": "$.answer", "code": "type", "message": "3 is not of type 'string'"},
    ]


def test_invalid_structured_result_is_typed_without_stage_or_termination_mutation():
    provider = _Provider('{"answer":3}')
    with pytest.raises(StructuredOutputError) as exc_info:
        LLMClient(_config(), {"fixture": provider}).complete(
            _request(), provider_name="fixture", model="model"
        )
    assert exc_info.value.errors[0]["code"] == "type"
    assert not hasattr(exc_info.value, "stage")


def test_one_repair_succeeds_and_aggregates_usage_metadata():
    provider = _RepairProvider(['{"answer":3}', 'repair prose {"answer":"fixed"}'])
    response = LLMClient(_config(), {"fixture": provider}).complete(
        _request(), provider_name="fixture", model="model"
    )

    assert response.parsed == {"answer": "fixed"}
    assert response.validation.value == "repaired"
    assert response.repair_attempts == 1
    assert response.tokens_in == 20
    assert response.tokens_out == 10
    assert len(provider.requests) == 2
    assert "Previous invalid output" in provider.requests[1][0].user
    assert "Validation errors" in provider.requests[1][0].user
    assert '"required"' in provider.requests[1][0].user


def test_second_invalid_response_raises_structured_failure_after_one_repair():
    provider = _RepairProvider(['{"answer":3}', '{"answer":4}'])
    with pytest.raises(StructuredOutputError) as exc_info:
        LLMClient(_config(), {"fixture": provider}).complete(
            _request(), provider_name="fixture", model="model"
        )

    assert len(provider.requests) == 2
    assert len(exc_info.value.responses) == 2
    assert exc_info.value.errors[0]["code"] == "type"
    assert not hasattr(exc_info.value, "stage")
