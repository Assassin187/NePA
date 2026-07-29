"""Capability probe 证据标准测试（设计文档 8.4、DEC-12）。"""

from __future__ import annotations

from typing import cast

import httpx
import pytest

from nepa.llm.capabilities import (
    CapabilityProbeError,
    probe_parameter_capabilities,
)
from nepa.llm.client import LLMClient, LLMError, LLMRequest, LLMResponse
from nepa.llm.providers.openai_compat import OpenAICompatProvider


class _ProbeClient:
    def __init__(
        self,
        response: LLMResponse | None = None,
        error: LLMError | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[LLMRequest, str, bool]] = []

    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
        use_cache: bool = True,
    ) -> LLMResponse:
        del task_id, attempt
        self.calls.append((req, stage, use_cache))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def test_accepted_request_without_provider_report_stays_unknown() -> None:
    client = _ProbeClient(
        LLMResponse(
            text="OK",
            model="returned-model",
            tokens_in=12,
            tokens_out=1,
            cost_usd=0.002,
            latency_ms=37,
            parameter_support={
                "temperature": "unknown",
                "max_tokens": "unknown",
            },
        )
    )

    result = probe_parameter_capabilities(
        client,
        provider="p",
        model="requested-model",
        temperature=0.0,
        max_tokens=8,
    )

    assert result.request_accepted is True
    assert result.parameter_support == {
        "temperature": "unknown",
        "max_tokens": "unknown",
    }
    assert result.evidence == {
        "temperature": "request_accepted_only",
        "max_tokens": "request_accepted_only",
    }
    assert result.response_model == "returned-model"
    assert result.error is None
    req, stage, use_cache = client.calls[0]
    assert req.json_schema is None
    assert req.temperature == 0.0 and req.max_tokens == 8
    assert stage == "capability_probe"
    assert use_cache is False


def test_explicit_provider_report_is_preserved() -> None:
    client = _ProbeClient(
        LLMResponse(
            text="OK",
            model="m",
            parameter_support={
                "temperature": "reported_ignored",
                "max_tokens": "reported_applied",
            },
        )
    )

    result = probe_parameter_capabilities(client, provider="p", model="m")

    assert result.parameter_support == {
        "temperature": "reported_ignored",
        "max_tokens": "reported_applied",
    }
    assert result.evidence == {
        "temperature": "provider_report",
        "max_tokens": "provider_report",
    }


def test_missing_parameter_report_is_conservatively_unknown() -> None:
    client = _ProbeClient(
        LLMResponse(
            text="OK",
            model="m",
            parameter_support={"temperature": "reported_applied"},
        )
    )

    result = probe_parameter_capabilities(client, provider="p", model="m")

    assert result.parameter_support["temperature"] == "reported_applied"
    assert result.evidence["temperature"] == "provider_report"
    assert result.parameter_support["max_tokens"] == "unknown"
    assert result.evidence["max_tokens"] == "request_accepted_only"


def test_failed_request_keeps_all_parameters_unknown() -> None:
    client = _ProbeClient(error=LLMError("provider unavailable"))

    result = probe_parameter_capabilities(client, provider="p", model="m")

    assert result.request_accepted is False
    assert result.response_model is None
    assert set(result.parameter_support.values()) == {"unknown"}
    assert set(result.evidence.values()) == {"no_response"}
    assert result.error is not None
    assert result.error.type == "LLMError"
    assert "unavailable" in result.error.message


def test_malformed_http_200_probe_fails_conservatively_as_unknown() -> None:
    provider = OpenAICompatProvider(
        base_url="https://api.example.com",
        api_key="key",
        model="requested-model",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"error": {"message": "upstream error"}},
            )
        ),
        retry_base_delay_s=0.0,
    )
    client = LLMClient(
        provider,
        provider_name="test",
        model="requested-model",
    )

    result = probe_parameter_capabilities(
        client,
        provider="test",
        model="requested-model",
    )

    assert result.request_accepted is False
    assert set(result.parameter_support.values()) == {"unknown"}
    assert set(result.evidence.values()) == {"no_response"}
    assert result.error is not None
    assert result.error.type == "ProviderResponseError"


def test_cached_probe_response_is_internal_error() -> None:
    client = _ProbeClient(LLMResponse(text="OK", model="m", cached=True))

    with pytest.raises(CapabilityProbeError, match="cached"):
        probe_parameter_capabilities(client, provider="p", model="m")


def test_probe_result_is_strict_and_json_serializable() -> None:
    client = _ProbeClient(LLMResponse(text="OK", model="m"))
    result = probe_parameter_capabilities(client, provider="p", model="m")

    dumped = result.model_dump(mode="json")
    assert dumped["schema_version"] == "1.0"
    assert cast(dict[str, str], dumped["evidence"])["temperature"] == (
        "request_accepted_only"
    )
