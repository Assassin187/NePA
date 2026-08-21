import json

import httpx
import pytest

from nepa.config import load_config
from nepa.llm.client import DecodingError, LLMConfigurationError, LLMRequest, ParameterSupportState, ProviderError, TransportError
from nepa.llm.providers.anthropic import AnthropicProvider
from nepa.llm.providers.openai_compat import DEFAULT_HTTP_TIMEOUT, OpenAICompatibleProvider


def _request():
    return LLMRequest(role="fixture", system="system", user="user", temperature=0.2, max_tokens=32)


def _event(value):
    return f"data: {json.dumps(value, separators=(',', ':'))}\n\n"


def _stream(*, model="returned/model-v2", chunks=("ans", "wer"), prompt_tokens=3, completion_tokens=4, finish_reason="stop"):
    events = [
        _event({"model": model, "choices": [{"delta": {"content": chunk}, "finish_reason": None}]})
        for chunk in chunks
    ]
    events.append(_event({"model": model, "choices": [{"delta": {}, "finish_reason": finish_reason}]}))
    events.append(_event({"model": model, "choices": [], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}))
    events.append("data: [DONE]\n\n")
    return "".join(events)


def _stream_response(**kwargs):
    return httpx.Response(200, text=_stream(**kwargs), headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("provider_name", ["qwen", "deepseek"])
def test_openai_compat_routes_qwen_and_deepseek_to_chat_completions(provider_name, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["payload"] = json.loads(request.read())
        return _stream_response()

    secret = f"secret-{provider_name}"
    monkeypatch.setenv(load_config().providers[provider_name].api_key_env, secret)
    provider = OpenAICompatibleProvider(
        provider_name,
        load_config().providers[provider_name],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.complete(_request(), model="fixture/model")

    assert seen["url"].endswith("/chat/completions")
    assert seen["authorization"] == f"Bearer {secret}"
    assert seen["payload"]["stream"] is True
    assert seen["payload"]["stream_options"] == {"include_usage": True}
    assert result.text == "answer"
    assert result.model == "returned/model-v2"
    assert result.tokens_in == 3
    assert result.tokens_out == 4
    assert result.parameter_support["temperature"] is ParameterSupportState.UNKNOWN
    assert secret not in repr(result)


def test_openai_compat_uses_native_schema_payload_only_when_explicit(monkeypatch):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.read()))
        return _stream_response(model="fixture/model", chunks=("{}",), prompt_tokens=1, completion_tokens=1)

    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")
    provider = OpenAICompatibleProvider(
        "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    request = _request().model_copy(update={"json_schema": {"type": "object"}})

    provider.complete(request, model="fixture/model", native_schema=True)

    assert "response_format" in payloads[0]
    assert "json_schema" in payloads[0]["response_format"]
    assert payloads[0]["stream"] is True
    assert payloads[0]["stream_options"] == {"include_usage": True}


def test_openai_compat_missing_secret_fails_before_http(monkeypatch):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    config = load_config()
    monkeypatch.delenv("NEPA_QWEN_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError):
        OpenAICompatibleProvider(
            "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")
    assert called is False


def test_openai_compat_malformed_success_is_typed_and_not_secret_bearing(monkeypatch):
    secret = "do-not-leak"

    def handler(request):
        return httpx.Response(200, text="data: {not-json}\n\n")

    config = load_config()
    monkeypatch.setenv(config.providers["deepseek"].api_key_env, secret)
    with pytest.raises(DecodingError) as exc_info:
        OpenAICompatibleProvider(
            "deepseek", config.providers["deepseek"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")
    assert secret not in str(exc_info.value)


def test_anthropic_exact_url_and_no_messages_path(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["has_x_api_key"] = "x-api-key" in request.headers
        seen["payload"] = json.loads(request.read())
        return _stream_response(model="claude-returned", chunks=("claude ", "answer"), prompt_tokens=5, completion_tokens=6)

    config = load_config()
    secret = "anthropic-secret"
    monkeypatch.setenv(config.providers["anthropic"].api_key_env, secret)
    AnthropicProvider(
        "anthropic", config.providers["anthropic"], client=httpx.Client(transport=httpx.MockTransport(handler))
    ).complete(_request(), model="claude-opus-5")

    assert seen["url"] == config.providers["anthropic"].base_url
    assert "/v1/messages" not in seen["url"]
    assert "/chat/completions/chat/completions" not in seen["url"]
    assert seen["authorization"] == f"Bearer {secret}"
    assert seen["has_x_api_key"] is False
    assert seen["payload"]["stream"] is True
    assert seen["payload"]["stream_options"] == {"include_usage": True}


def test_anthropic_alternate_url_is_used_exactly(monkeypatch):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return _stream_response(model="fixture", chunks=("ok",), prompt_tokens=1, completion_tokens=2)

    config = load_config(overrides={"providers": {"anthropic": {"base_url": "https://alternate.test/custom-endpoint"}}})
    monkeypatch.setenv(config.providers["anthropic"].api_key_env, "fixture-secret")
    AnthropicProvider(
        "anthropic", config.providers["anthropic"], client=httpx.Client(transport=httpx.MockTransport(handler))
    ).complete(_request(), model="fixture")

    assert urls == ["https://alternate.test/custom-endpoint"]


def test_anthropic_no_messages_secret_is_redacted_from_failure(monkeypatch):
    secret = "anthropic-do-not-leak"

    def handler(request):
        return httpx.Response(401, text=f"bad key {secret}")

    config = load_config()
    monkeypatch.setenv(config.providers["anthropic"].api_key_env, secret)
    with pytest.raises(ProviderError) as exc_info:
        AnthropicProvider(
            "anthropic", config.providers["anthropic"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="claude-opus-5")
    assert secret not in str(exc_info.value)


def test_anthropic_normalize_response(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["anthropic"].api_key_env, "fixture-secret")

    def handler(request):
        return _stream_response(model="claude-v3.1", chunks=("normal", "ized"), prompt_tokens=7, completion_tokens=8)

    response = AnthropicProvider(
        "anthropic", config.providers["anthropic"], client=httpx.Client(transport=httpx.MockTransport(handler))
    ).complete(_request(), model="claude-opus-5")
    assert response.text == "normalized"
    assert response.model == "claude-v3.1"
    assert response.tokens_in == 7
    assert response.tokens_out == 8


def test_stream_chunks_usage_done_finish_reason_and_model_identity(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")
    provider = OpenAICompatibleProvider(
        "qwen",
        config.providers["qwen"],
        client=httpx.Client(transport=httpx.MockTransport(lambda request: _stream_response(model="qwen-v1", chunks=("a", "b", "c"), prompt_tokens=11, completion_tokens=12))),
    )

    result = provider.complete(_request(), model="fixture/model")

    assert result.text == "abc"
    assert result.model == "qwen-v1"
    assert result.tokens_in == 11
    assert result.tokens_out == 12
    assert result.provider_metadata["finish_reason"] == "stop"


def test_stream_requires_usage_and_done(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["deepseek"].api_key_env, "fixture-secret")

    def handler(request):
        return httpx.Response(
            200,
            text=_event({"model": "deepseek-v1", "choices": [{"delta": {"content": "partial"}, "finish_reason": "stop"}]}) + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(DecodingError, match="final usage"):
        OpenAICompatibleProvider(
            "deepseek", config.providers["deepseek"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")


def test_stream_requires_done_marker(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["deepseek"].api_key_env, "fixture-secret")

    def handler(request):
        return httpx.Response(
            200,
            text=_event({"model": "deepseek-v1", "choices": [{"delta": {"content": "partial"}, "finish_reason": "stop"}]})
            + _event({"model": "deepseek-v1", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(DecodingError, match=r"before \[DONE\]"):
        OpenAICompatibleProvider(
            "deepseek", config.providers["deepseek"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")


@pytest.mark.parametrize("status_code", [301, 400, 401, 429, 500, 503])
def test_stream_non_2xx_is_typed_and_retryability_is_preserved(monkeypatch, status_code):
    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")

    def handler(request):
        return httpx.Response(status_code, text="provider error with no secret")

    with pytest.raises(ProviderError) as exc_info:
        OpenAICompatibleProvider(
            "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")
    assert exc_info.value.status_code == status_code
    assert exc_info.value.retryable is (status_code == 429 or status_code >= 500)


@pytest.mark.parametrize(
    "stream_text",
    [
        "event: message\n\n",
        "data: {not-json}\n\n",
        "data: []\n\n",
        "data: {\"model\":\"fixture\",\"choices\":{}}\n\n",
    ],
)
def test_malformed_sse_is_decoding_error_without_secret(monkeypatch, stream_text):
    config = load_config()
    secret = "provider-secret-not-in-error"
    monkeypatch.setenv(config.providers["qwen"].api_key_env, secret)

    def handler(request):
        return httpx.Response(200, text=stream_text, headers={"content-type": "text/event-stream"})

    with pytest.raises(DecodingError) as exc_info:
        OpenAICompatibleProvider(
            "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")
    assert secret not in str(exc_info.value)


def test_stream_model_identity_must_be_stable(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")

    def handler(request):
        body = _event({"model": "fixture-v1", "choices": [{"delta": {"content": "a"}, "finish_reason": None}]})
        body += _event({"model": "fixture-v2", "choices": [{"delta": {"content": "b"}, "finish_reason": None}]})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    with pytest.raises(DecodingError, match="model identity changed"):
        OpenAICompatibleProvider(
            "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")


def test_stream_read_timeout_is_transport_error(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")

    def handler(request):
        raise httpx.ReadTimeout("stream idle", request=request)

    with pytest.raises(TransportError, match="ReadTimeout"):
        OpenAICompatibleProvider(
            "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).complete(_request(), model="fixture/model")


def test_default_provider_timeout_is_explicit(monkeypatch):
    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")
    provider = OpenAICompatibleProvider("qwen", config.providers["qwen"])
    try:
        assert provider.client.timeout.connect == DEFAULT_HTTP_TIMEOUT.connect == 10.0
        assert provider.client.timeout.read == DEFAULT_HTTP_TIMEOUT.read == 300.0
        assert provider.client.timeout.write == DEFAULT_HTTP_TIMEOUT.write == 60.0
        assert provider.client.timeout.pool == DEFAULT_HTTP_TIMEOUT.pool == 60.0
    finally:
        provider.client.close()
