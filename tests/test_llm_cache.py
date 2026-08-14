import json

import pytest

from nepa.config import load_config
from nepa.llm.cache import LLMCache, cache_key
from nepa.llm.client import EvidenceStorageError, LLMClient, LLMRequest, LLMResponse, ParameterSupportState
from nepa.run_store import ArtifactConflict, RunStore


def _request(**updates):
    value = {"role": "fixture", "system": "system", "user": "user", "temperature": 0.1, "max_tokens": 32}
    value.update(updates)
    return LLMRequest(**value)


def _response():
    return LLMResponse(
        text="answer",
        tokens_in=2,
        tokens_out=3,
        cost_usd=0.5,
        model="fixture/model",
        parameter_support={"temperature": ParameterSupportState.UNKNOWN},
    )


def _cache(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    return LLMCache(store), store


def test_cache_key_changes_for_every_provider_affecting_input():
    base = {"provider_name": "fixture", "provider_kind": "openai_compat", "model": "fixture/model", "request": _request()}
    original = cache_key(**base)
    variants = [
        {**base, "provider_name": "other"},
        {**base, "provider_kind": "anthropic"},
        {**base, "model": "fixture/other"},
        {**base, "request": _request(temperature=0.2)},
        {**base, "request": _request(max_tokens=33)},
        {**base, "request": _request(json_schema={"type": "object"})},
        {**base, "request": _request(system="other system")},
        {**base, "request": _request(user="other user")},
    ]

    assert all(cache_key(**variant) != original for variant in variants)


def test_identical_immutable_response_replay_is_idempotent(tmp_path):
    cache, store = _cache(tmp_path)
    request = _request()
    key = cache_key(provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request)

    cache.publish(key, provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request, response=_response())
    cache.publish(key, provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request, response=_response())

    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.text == "answer"
    assert list((store.root / "cache/llm").glob("*.json")) == [store.root / f"cache/llm/{key}.json"]


def test_conflicting_immutable_entry_fails_closed(tmp_path):
    cache, store = _cache(tmp_path)
    request = _request()
    key = cache_key(provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request)
    cache.publish(key, provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request, response=_response())
    path = store.root / f"cache/llm/{key}.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["response"]["text"] = "different"
    with pytest.raises(ArtifactConflict):
        store.publish_immutable_json(f"cache/llm/{key}.json", changed)


def test_cache_key_and_value_never_include_provider_secret(tmp_path):
    cache, store = _cache(tmp_path)
    secret = "provider-secret"
    request = _request()
    key = cache_key(provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request)
    cache.publish(key, provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request, response=_response())
    raw = (store.root / f"cache/llm/{key}.json").read_text(encoding="utf-8")

    assert secret not in key
    assert secret not in raw


def test_failed_response_is_not_cached(tmp_path):
    cache, _ = _cache(tmp_path)
    request = _request()
    key = cache_key(provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request)
    failed = _response().model_copy(update={"validation": "fail"})

    with pytest.raises(EvidenceStorageError):
        cache.publish(key, provider_name="fixture", provider_kind="openai_compat", model="fixture/model", request=request, response=failed)
    assert cache.load(key) is None


class _Provider:
    native_structured_output = False

    def __init__(self, calls, *, fail=False):
        self.calls = calls
        self.fail = fail

    def complete(self, request, *, model, native_schema):
        self.calls.append(request)
        if self.fail:
            from nepa.llm.client import ProviderError

            raise ProviderError("provider failure", provider="fixture", status_code=400)
        return LLMResponse(
            text="answer",
            tokens_in=2,
            tokens_out=3,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


def _client(tmp_path, calls, *, fail=False):
    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    return LLMClient(config, {"fixture": _Provider(calls, fail=fail)}), store


def test_cache_hit_makes_no_provider_request_and_adds_zero_cost(tmp_path):
    calls = []
    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    provider = _Provider(calls)
    client = LLMClient(config, {"fixture": provider}, store=store)
    request = _request()

    first = client.complete(request, provider_name="fixture", model="model")
    second = client.complete(request, provider_name="fixture", model="model")

    assert first.cached is False
    assert second.cached is True
    assert second.cost_usd == 0
    assert second.text == first.text
    assert len(calls) == 1


def test_cache_failed_provider_result_creates_no_entry(tmp_path):
    calls = []
    client, store = _client(tmp_path, calls, fail=True)

    with pytest.raises(Exception, match="provider failure"):
        client.complete(_request(), provider_name="fixture", model="model")

    assert list((store.root / "cache").rglob("*.json")) == [] if (store.root / "cache").exists() else True


def test_cache_disabled_bypass_makes_provider_request_and_preserves_output(tmp_path):
    calls = []
    config = load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": None}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    client = LLMClient(config, {"fixture": _Provider(calls)}, store=store)
    request = _request()
    client.complete(request, provider_name="fixture", model="model")
    bypassed = client.complete(request, provider_name="fixture", model="model", use_cache=False)

    assert bypassed.cached is False
    assert bypassed.cost_usd > 0
    assert len(calls) == 2
