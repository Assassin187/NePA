import httpx
import pytest

from nepa.config import load_config
from nepa.llm.client import LLMClient, LLMRequest, LLMResponse, ProviderError, TransportError


class FakeProvider:
    native_structured_output = False

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def complete(self, request, *, model, native_schema):
        self.calls += 1
        failure = self.failures.pop(0) if self.failures else None
        if failure is not None:
            raise failure
        return LLMResponse(
            text="ok",
            tokens_in=2,
            tokens_out=3,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": "unknown"},
        )


def _client(provider, sleeps, backoffs, admissions=None):
    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 0, "output_usd_per_million_tokens": 0}}},
        }
    )
    orchestrator = None
    store = None
    if admissions is not None:
        class Admission:
            def admit_external_call(self, run_store):
                admissions.append(run_store)
        orchestrator = Admission()
        store = object()
    return LLMClient(
        config,
        {"fixture": provider},
        orchestrator=orchestrator,
        store=store,
        sleeper=sleeps.append,
        backoff=lambda number: backoffs[number - 1],
    )


def _request():
    return LLMRequest(role="fixture", system="system", user="user", temperature=0, max_tokens=20)


def test_retry_recovers_after_network_429_and_5xx_with_three_bounded_retries():
    provider = FakeProvider([
        TransportError("network", provider="fixture"),
        ProviderError("rate limited", provider="fixture", status_code=429),
        ProviderError("server", provider="fixture", status_code=503),
    ])
    sleeps = []
    backoffs = [0.1, 0.2, 0.4]
    client = _client(provider, sleeps, backoffs)

    response = client.complete(_request(), provider_name="fixture", model="model")

    assert response.text == "ok"
    assert response.transport_attempts == 4
    assert provider.calls == 4
    assert sleeps == backoffs


def test_retry_limit_is_four_total_attempts():
    provider = FakeProvider([TransportError("network", provider="fixture")] * 4)
    sleeps = []
    client = _client(provider, sleeps, [1, 2, 4])

    with pytest.raises(TransportError) as exc_info:
        client.complete(_request(), provider_name="fixture", model="model")

    assert provider.calls == 4
    assert sleeps == [1, 2, 4]
    assert exc_info.value.attempts == 4


def test_other_4xx_is_not_retried_and_admission_precedes_each_attempt():
    provider = FakeProvider([ProviderError("bad request", provider="fixture", status_code=400)])
    sleeps = []
    admissions = []
    client = _client(provider, sleeps, [1], admissions)

    with pytest.raises(ProviderError) as exc_info:
        client.complete(_request(), provider_name="fixture", model="model")

    assert exc_info.value.retryable is False
    assert provider.calls == 1
    assert sleeps == []
    assert len(admissions) == 1


def test_malformed_success_is_not_a_transport_retry():
    class Malformed(FakeProvider):
        def complete(self, request, *, model, native_schema):
            self.calls += 1
            raise __import__("nepa.llm.client", fromlist=["DecodingError"]).DecodingError("bad envelope")

    provider = Malformed([])
    sleeps = []
    client = _client(provider, sleeps, [1])

    with pytest.raises(Exception, match="bad envelope"):
        client.complete(_request(), provider_name="fixture", model="model")
    assert provider.calls == 1
    assert sleeps == []
