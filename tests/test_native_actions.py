import json
from pathlib import Path
import httpx
import pytest
from nepa.agents.context import CodingContext
from nepa.config import CoderConfig, ConfigError, load_config
from nepa.llm.client import LLMClient, LLMRequest, LLMResponse, decode_action
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
from nepa.run_store import RunStore
from nepa.schemas import load_schema

ROOT = Path(__file__).parents[1]
SCHEMA = load_schema('agent-action.schema.json')


def response(name='write_file', arguments='{"path":"a.c","content":"hello"}', count=1, reason='tool_calls'):
    return LLMResponse(text='', tool_calls=[{'id': f'call_{i}', 'type': 'function', 'function': {'name': name, 'arguments': arguments}} for i in range(count)],
                       reasoning_content='provider reasoning fixture', tokens_in=12, tokens_out=15, cost_cny=0,
                       model='deepseek-v4-pro', parameter_support={}, provider_metadata={'finish_reason': reason})


@pytest.mark.parametrize('value', [response(count=0), response(count=2), response(name='unknown'),
    response(arguments='{"path":"a.c"}'), response(arguments='{"path":"a.c","content":"x"'),
    response(arguments='<invoke/>'), response(reason='length')])
def test_invalid_native_actions_execute_nothing(value):
    assert decode_action(value, 'tool_calls', SCHEMA)[1]


def test_complete_native_action_uses_unchanged_local_schema():
    action, errors = decode_action(response(), 'tool_calls', SCHEMA)
    assert errors == [] and action == {'tool': 'write_file', 'arguments': {'path': 'a.c', 'content': 'hello'}}
    with pytest.raises(ConfigError):
        load_config(overrides={'schema_version': '1.0'})
    with pytest.raises(ConfigError):
        load_config(overrides={'coder': {'json_output': True}})


def test_native_stream_assembly_real_http_and_accounting(tmp_path, monkeypatch):
    monkeypatch.setenv('NEPA_DS_API_KEY', 'fixture-secret')
    deltas = [
        {'reasoning_content': 'provider '},
        {'reasoning_content': 'reasoning'},
        {'tool_calls': [{'index': 0, 'id': 'call_0', 'type': 'function', 'function': {'name': 'write_', 'arguments': '{"path":"a.c",'}}]},
        {'tool_calls': [{'index': 0, 'function': {'name': 'file', 'arguments': '"content":"a\\n'}}]},
        {'tool_calls': [{'index': 0, 'function': {'arguments': 'b"}'}}]},
    ]
    events = [{'choices': [{'delta': d, 'finish_reason': None}]} for d in deltas]
    events.append({'model': 'deepseek-v4-pro', 'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}],
                   'usage': {'prompt_tokens': 123, 'completion_tokens': 456, 'prompt_cache_hit_tokens': 100, 'prompt_cache_miss_tokens': 23}})
    seen = []
    def handler(request):
        payload = json.loads(request.read()); seen.append(payload)
        assert 'response_format' not in payload and payload['tool_choice'] == 'auto'
        return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n')
    config = load_config(overrides={'coder': {'action_format': 'tool_calls'}})
    provider = OpenAICompatibleProvider('deepseek', config.providers['deepseek'], client=httpx.Client(transport=httpx.MockTransport(handler)))
    store = RunStore.initialize(tmp_path, ROOT / 'gold_file/mqtt/specIR.json', ROOT / 'gold_file/mqtt/target.json', ROOT / 'gold_file/mqtt/acceptance.json', config)
    request = LLMRequest(role='coder', system='native tools', user='write file', action_format='tool_calls', json_schema=SCHEMA, temperature=0, max_tokens=16000)
    result = LLMClient(config, {'deepseek': provider}).complete(request, store=store, task_id='bootstrap')
    assert result.reasoning_content == 'provider reasoning'
    assert result.pricing['cache_hit_tokens'] == 100 and result.pricing['cache_miss_tokens'] == 23
    assert result.pricing['cache_usage_basis'] == 'provider'
    assert store.run['budget']['settled_cny'][result.pricing['period']] == result.cost_cny
    assert decode_action(result, 'tool_calls', SCHEMA)[0]['arguments']['content'] == 'a\nb'
    saved = json.loads((store.root / 'evidence/calls/000001.request.json').read_text())
    assert saved['wire'] == seen[0]
    assert not store.run['pending_calls'] and store.run['budget']['tokens_out'] == 456


def test_native_transactions_reasoning_pairing_and_eviction():
    context = CodingContext('s', {'task': {}}, CoderConfig(action_format='tool_calls'), None, SCHEMA)
    value = response(count=2)
    context.record(value, {'format_errors': ['one action only']})
    req = context.request({})
    assert [m['role'] for m in req.messages] == ['user', 'assistant', 'tool', 'tool']
    assert [m['tool_call_id'] for m in req.messages[2:]] == ['call_0', 'call_1']
    assert req.messages[1]['reasoning_content'] == value.reasoning_content
    context.record(response(), {'tool_result': {'ok': True}})
    context.coder.context_max_bytes = len(json.dumps(OpenAICompatibleProvider._payload(req, req.model, False), ensure_ascii=False).encode())
    trimmed = context.request({})
    assert context.evicted_transactions == 1
    assert [m['role'] for m in trimmed.messages] == ['user', 'assistant', 'tool']
    assert trimmed.messages[-1]['tool_call_id'] == trimmed.messages[-2]['tool_calls'][0]['id']


@pytest.mark.sandbox_integration
def test_native_session_rejects_invalid_calls_then_repairs_real_compiler(tmp_path):
    from nepa.application import build_orchestrator
    from test_execution_pipeline import MAKEFILE
    config = load_config(overrides={'coder': {'action_format': 'tool_calls'}, 'budgets': {'decisions_per_session': 10}})
    store = RunStore.initialize(tmp_path, ROOT / 'gold_file/mqtt/specIR.json', ROOT / 'gold_file/mqtt/target.json', ROOT / 'gold_file/mqtt/acceptance.json', config)
    values = [response(arguments=json.dumps({'path': 'Makefile', 'content': MAKEFILE})),
              response(count=0), response(count=2), response(name='write_file', arguments='{}'),
              response(arguments=json.dumps({'path': 'main.c', 'content': 'int main(void){ broken }'})),
              response(name='finish', arguments='{"summary":"check compilation","claims":[]}'),
              response(arguments=json.dumps({'path': 'main.c', 'content': 'int main(void){return 0;}'})),
              response(name='finish', arguments='{"summary":"fixed actual diagnostic","claims":[]}')]
    class Provider:
        native_structured_output = False
        def complete(self, request, **kwargs):
            return values.pop(0)
    session = build_orchestrator(store, {'deepseek': Provider()}).session
    assert session.run(store.plan()['tasks'][0])
    assert store.run['budget']['calls'] == 8
    assert len(list((store.root / 'evidence/actions').glob('*.json'))) == 5
    assert (store.project / 'build/san/protocol-server').is_file()
