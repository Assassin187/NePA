import httpx
import pytest

from nepa.config import load_config
from nepa.llm.client import DecodingError, LLMConfigurationError, LLMRequest, ParameterSupportState, ProviderError
from nepa.llm.providers.anthropic import AnthropicProvider
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider


def _request():
    return LLMRequest(role="fixture", system="system", user="user", temperature=0.2, max_tokens=32)


@pytest.mark.parametrize("provider_name", ["qwen", "deepseek"])
def test_openai_compat_routes_qwen_and_deepseek_to_chat_completions(provider_name, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "returned/model-v2",
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

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
    assert result.text == "answer"
    assert result.model == "returned/model-v2"
    assert result.tokens_in == 3
    assert result.tokens_out == 4
    assert result.parameter_support["temperature"] is ParameterSupportState.UNKNOWN
    assert secret not in repr(result)


def test_openai_compat_uses_native_schema_payload_only_when_explicit(monkeypatch):
    payloads = []

    def handler(request):
        payloads.append(request.read())
        return httpx.Response(
            200,
            json={
                "model": "fixture/model",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    config = load_config()
    monkeypatch.setenv(config.providers["qwen"].api_key_env, "fixture-secret")
    provider = OpenAICompatibleProvider(
        "qwen", config.providers["qwen"], client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    request = _request().model_copy(update={"json_schema": {"type": "object"}})

    provider.complete(request, model="fixture/model", native_schema=True)

    assert b'"response_format"' in payloads[0]
    assert b'"json_schema"' in payloads[0]


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
        return httpx.Response(200, json={"choices": [], "secret": secret})

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
        seen["header"] = request.headers["x-api-key"]
        return httpx.Response(
            200,
            json={
                "model": "claude-returned",
                "choices": [{"message": {"content": "claude answer"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 6},
            },
        )

    config = load_config()
    secret = "anthropic-secret"
    monkeypatch.setenv(config.providers["anthropic"].api_key_env, secret)
    AnthropicProvider(
        "anthropic", config.providers["anthropic"], client=httpx.Client(transport=httpx.MockTransport(handler))
    ).complete(_request(), model="claude-opus-5")

    assert seen["url"] == config.providers["anthropic"].base_url
    assert "/v1/messages" not in seen["url"]
    assert "/chat/completions/chat/completions" not in seen["url"]
    assert seen["header"] == secret


def test_anthropic_alternate_url_is_used_exactly(monkeypatch):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"model": "fixture", "choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
        )

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
        return httpx.Response(
            200,
            json={
                "model": "claude-v3.1",
                "choices": [{"message": {"content": "normalized"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 8},
            },
        )

    response = AnthropicProvider(
        "anthropic", config.providers["anthropic"], client=httpx.Client(transport=httpx.MockTransport(handler))
    ).complete(_request(), model="claude-opus-5")
    assert response.text == "normalized"
    assert response.model == "claude-v3.1"
    assert response.tokens_in == 7
    assert response.tokens_out == 8
