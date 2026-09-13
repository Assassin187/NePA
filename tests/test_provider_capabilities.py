"""Offline admission for provider-owned wire bytes and strict observed envelopes."""
import json
from pathlib import Path

import httpx
import pytest

from nepa.config import ConfigError, load_config
from nepa.llm.client import (LLMClient, LLMConfigurationError, LLMRequest, LLMRequestError,
                             ResponseIdentityError, ResponseUsageError, DecodingError, decode_action)
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
from nepa.run_store import RunStore
from nepa.schemas import load_schema

ROOT = Path(__file__).parents[1]
PLUS = 'qwen3.7-plus-2026-05-26'
FLASH = 'qwen3.7-flash-2026-07-15'
SCHEMA = load_schema('agent-action.schema.json')


def qwen_config(**overrides):
    return load_config(ROOT / 'configs/qwen-p0-p2.yaml', overrides=overrides)


def request(model=PLUS, **changes):
    values = dict(role='coder', model=model, system='Return one JSON action.', user='中文 😀',
                  temperature=0, max_tokens=16000, action_format='tool_calls', json_schema=SCHEMA)
    values.update(changes)
    return LLMRequest(**values)


def events(model=PLUS, usage=None, *, include_usage=True, done=True):
    data = [dict(choices=[dict(delta={'reasoning_content': 'reasoning observation'}, finish_reason=None)]),
            dict(model=model, choices=[dict(delta={'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                 'function': {'name': 'write_file', 'arguments': '{"path":"a.c",'}}]}, finish_reason=None)]),
            dict(choices=[dict(delta={'tool_calls': [{'index': 0, 'function': {'arguments': '"content":"test"}'}}]},
                               finish_reason='tool_calls')])]
    if include_usage:
        data.append(dict(choices=[], usage=usage if usage is not None else dict(prompt_tokens=100, completion_tokens=20,
                         prompt_tokens_details={'cached_tokens': 80}, completion_tokens_details={'reasoning_tokens': 12})))
    return ''.join('data: ' + json.dumps(value) + '\n\n' for value in data) + ('data: [DONE]\n\n' if done else '')


def client_for(config, handler):
    provider = OpenAICompatibleProvider('qwen', config.providers['qwen'],
        client=httpx.Client(transport=httpx.MockTransport(handler)), env_lookup=lambda _: 'offline-fixture-key')
    return LLMClient(config, {'qwen': provider})


def store_for(tmp_path, config):
    return RunStore.initialize(tmp_path, ROOT / 'gold_file/mqtt/specIR.json', ROOT / 'gold_file/mqtt/target.json',
                               ROOT / 'gold_file/mqtt/acceptance.json', config)


@pytest.mark.parametrize('model', [PLUS, FLASH])
@pytest.mark.parametrize('mode', ['tool_calls', 'json_object'])
def test_prepared_wire_is_exact_http_bytes_and_reservation(tmp_path, model, mode):
    config = qwen_config()
    seen = []
    def handler(req):
        seen.append(req.read())
        return httpx.Response(200, text=events(model))
    client = client_for(config, handler)
    req = request(model, action_format=mode)
    prepared = client.prepare(req)
    wire = prepared.wire
    assert prepared.wire_bytes == len(prepared.body) > len(prepared.body.decode('utf-8'))
    assert prepared.input_reserve_tokens >= prepared.wire_bytes
    assert prepared.billable_output_tokens == 16010
    assert wire['enable_thinking'] is wire['preserve_thinking'] is True
    assert wire['max_completion_tokens'] == 16000 and 'max_tokens' not in wire
    assert 'thinking' not in wire and 'reasoning_effort' not in wire
    assert wire['stream_options'] == {'include_usage': True}
    if mode == 'tool_calls':
        assert wire['tool_choice'] == 'auto' and wire['parallel_tool_calls'] is False
        assert 'response_format' not in wire
        assert [t['function']['parameters'] for t in wire['tools']] == [v['properties']['arguments'] for v in SCHEMA['oneOf']]
    else:
        assert wire['response_format'] == {'type': 'json_object'} and 'tools' not in wire
    store = store_for(tmp_path, config)
    result = client.complete(req, prepared=prepared, store=store, task_id='bootstrap')
    assert seen == [prepared.body]
    evidence = json.loads((store.root / 'evidence/calls/000001.request.json').read_text())
    assert evidence['wire'] == wire == json.loads(seen[0])
    assert result.model == model and result.tokens_out == 20
    assert result.pricing['period'] == 'flat'
    assert result.pricing['cache_hit_tokens'] == 80
    assert result.provider_metadata['reasoning_tokens'] == 12
    assert result.cost_cny == pytest.approx((80 * (0.4 if model == PLUS else .04) + 20 * (2 if model == PLUS else .2) + 20 * (8 if model == PLUS else .8)) / 1e6)
    assert not store.run['pending_calls']


@pytest.mark.parametrize('changes,exc', [
    ({'temperature': 2}, LLMRequestError), ({'stop': ['END']}, LLMConfigurationError),
    ({'max_tokens': 131073}, LLMRequestError), ({'model': 'qwen-plus'}, LLMConfigurationError),
    ({'action_format': 'json_object', 'system': 'one action'}, LLMRequestError),
])
def test_unsupported_requests_fail_before_io(changes, exc):
    client = client_for(qwen_config(), lambda _: pytest.fail('unexpected HTTP'))
    with pytest.raises(exc):
        client.prepare(request(**changes))


@pytest.mark.parametrize('capability', ['stream', 'json_object', 'tool_calls'])
def test_explicit_capabilities_reject_unsupported_mode(capability):
    config = qwen_config(capabilities={'qwen/' + PLUS: {capability: False}})
    client = client_for(config, lambda _: pytest.fail('unexpected HTTP'))
    with pytest.raises(LLMConfigurationError):
        client.prepare(request(action_format='json_object' if capability == 'json_object' else 'tool_calls'))


def test_config3_exact_models_and_task_routing():
    config = qwen_config()
    assert config.schema_version == '3.0'
    assert 'runs_root' not in config.campaign.model_dump()
    assert config.campaign.phase_max_cost_cny == {'capability': 5, 'public_tools': 5}
    for kind in ['bootstrap', 'message', 'requirements']:
        assert config.coder.for_task(kind).model == FLASH
        assert config.coder.for_task(kind, retry=True).model == PLUS
        assert config.coder.for_task(kind, repair=True).model == PLUS
    assert config.coder.for_task('shared-wire').model == config.coder.for_task('final-integration').model == PLUS
    for overlay in [{'schema_version': '2.0'}, {'coder': {'model': 'qwen-plus'}},
                    {'capabilities': {'qwen/' + PLUS: {'wire': {'arbitrary_extra_body': {}}}}},
                    {'campaign': {'phase_max_cost_cny': {'capability': 6}}},
                    {'campaign': {'runs_root': 'runs/qwen-e2e'}}]:
        with pytest.raises(ConfigError):
            qwen_config(**overlay)


@pytest.mark.parametrize('body,error', [
    (events(model=None), ResponseIdentityError), (events(model='qwen-plus'), ResponseIdentityError),
    (events(include_usage=False), ResponseUsageError),
    (events(usage={'prompt_tokens': 100, 'completion_tokens': -1}), ResponseUsageError),
    (events(usage={'prompt_tokens': True, 'completion_tokens': 20}), ResponseUsageError),
    (events(usage={'prompt_tokens': 100, 'completion_tokens': 20, 'prompt_tokens_details': {'cached_tokens': 101}}), ResponseUsageError),
    (events(usage={'prompt_tokens': 100, 'completion_tokens': 20, 'completion_tokens_details': {'reasoning_tokens': 21}}), ResponseUsageError),
    (events(done=False), DecodingError),
])
def test_observed_fault_keeps_entire_response_and_reservation(tmp_path, body, error):
    config = qwen_config(campaign={'phase': 'capability'})
    attempts = []
    def handler(req):
        attempts.append(req)
        return httpx.Response(200, text=body)
    client = client_for(config, handler)
    store = store_for(tmp_path, config)
    reserved = client.prepare(request()).reservation_cny
    with pytest.raises(error) as caught:
        client.complete(request(), store=store, task_id='probe')
    assert not caught.value.retryable and caught.value.observed_raw_response['sse'] == body
    assert len(attempts) == store.run['budget']['calls'] == 1
    assert store.run['pending_calls']['1']['reserved'] == reserved
    assert store.run['budget']['cost_cny'] == reserved
    assert store.run['phase_cost_cny']['capability'] == reserved
    assert store.run['budget']['tokens_in'] == store.run['budget']['tokens_out'] == 0
    raw = json.loads((store.root / 'evidence/calls/000001.fault-response.json').read_text())
    assert raw['sse'] == body
    assert 'call_1' in raw['sse'] and 'reasoning observation' in raw['sse']
    assert not (store.root / 'evidence/calls/000001.response.json').exists()


def test_optional_usage_details_are_explicit_unknown_all_miss(tmp_path):
    config = qwen_config()
    client = client_for(config, lambda _: httpx.Response(200, text=events(usage={'prompt_tokens': 100, 'completion_tokens': 20})))
    result = client.complete(request(), store=store_for(tmp_path, config), task_id='bootstrap')
    assert result.provider_metadata['reasoning_tokens'] is None
    assert result.provider_metadata['reasoning_usage_basis'] == 'unknown'
    assert result.pricing['cache_usage_basis'] == 'missing_assumed_all_miss'
    assert result.pricing['cache_miss_tokens'] == 100


def test_native_reasoning_and_fragmented_arguments_roundtrip():
    config = qwen_config()
    client = client_for(config, lambda _: httpx.Response(200, text=events()))
    provider = client.providers['qwen']
    response = provider.send(client.prepare(request()))
    action, errors = decode_action(response, 'tool_calls', SCHEMA)
    assert not errors and action['arguments'] == {'path': 'a.c', 'content': 'test'}
    next_request = request(messages=[{'role': 'user', 'content': 'write'}, response.assistant_message(),
        {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'written'}])
    wire = client.prepare(next_request).wire
    assert wire['messages'][2]['reasoning_content'] == 'reasoning observation'
    assert wire['messages'][2]['tool_calls'][0]['function']['arguments'] == '{"path":"a.c","content":"test"}'
    assert wire['messages'][3]['tool_call_id'] == 'call_1'
    response.provider_metadata['finish_reason'] = 'length'
    assert decode_action(response, 'tool_calls', SCHEMA)[0] is None


def test_prepared_request_cannot_survive_model_or_context_change(tmp_path):
    config = qwen_config()
    client = client_for(config, lambda _: pytest.fail('stale prepared request sent'))
    store = store_for(tmp_path, config)
    original = request(FLASH)
    prepared = client.prepare(original)
    for changed in [request(PLUS), request(FLASH, user='changed context')]:
        with pytest.raises(LLMRequestError, match='does not match'):
            client.complete(changed, prepared=prepared, store=store, task_id='bootstrap')
    assert store.run['budget']['calls'] == 0


def test_complete_prepares_once_or_uses_context_preparation_unchanged(tmp_path, monkeypatch):
    config = qwen_config()
    client = client_for(config, lambda _: httpx.Response(200, text=events()))
    store = store_for(tmp_path, config)
    original_prepare = client.prepare
    calls = []
    def prepare(req):
        calls.append(req)
        return original_prepare(req)
    monkeypatch.setattr(client, 'prepare', prepare)
    client.complete(request(), store=store, task_id='bootstrap')
    assert len(calls) == 1
    prepared = client.prepare(request())
    client.complete(request(), prepared=prepared, store=store, task_id='bootstrap')
    assert len(calls) == 2


def test_full_sse_event_fields_multiline_json_and_unicode_separator():
    config = qwen_config()
    body = ': heartbeat\r\nid: 1\r\nevent: message\r\nretry: 1000\r\n'
    body += 'data: {"model": "' + PLUS + '",\r\n'
    body += 'data: "choices": [{"delta": {"content": "a\u2028b"}, "finish_reason": "stop"}]}\r\n\r\n'
    body += 'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
    client = client_for(config, lambda _: httpx.Response(200, text=body))
    result = client.providers['qwen'].send(client.prepare(request()))
    assert result.text == 'a\u2028b'
    assert result.provider_metadata['observed_raw_response']['sse'] == body


def test_fault_evidence_uses_store_secret_redaction(tmp_path, monkeypatch):
    secret = 'offline-evidence-redaction-key'
    monkeypatch.setenv('NEPA_QWEN_API_KEY', secret)
    config = qwen_config()
    body = events(include_usage=False).replace('reasoning observation', secret)
    client = client_for(config, lambda _: httpx.Response(200, text=body))
    store = store_for(tmp_path, config)
    with pytest.raises(ResponseUsageError):
        client.complete(request(), store=store, task_id='probe')
    saved = (store.root / 'evidence/calls/000001.fault-response.json').read_text()
    assert secret not in saved and '[REDACTED]' in saved


def test_deadline_interruption_preserves_partial_observation(tmp_path):
    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial action"}}]}\n\n'
            raise TimeoutError('deadline alarm')
    config = qwen_config()
    client = client_for(config, lambda _: httpx.Response(200, stream=InterruptedStream()))
    store = store_for(tmp_path, config)
    with pytest.raises(TimeoutError):
        client.complete(request(), store=store, task_id='probe')
    saved = (store.root / 'evidence/calls/000001.fault-response.json').read_text()
    assert 'partial action' in saved
    assert store.run['budget']['calls'] == len(store.run['pending_calls']) == 1


def test_native_wire_stable_after_persisted_request_key_sorting():
    client = LLMClient(qwen_config())
    original = request()
    restored = LLMRequest.model_validate(json.loads(json.dumps(original.model_dump(mode='json'), sort_keys=True)))
    assert client.prepare(original).body == client.prepare(restored).body
