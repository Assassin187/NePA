"""Provider pricing and ledger admission; no paid requests."""
from datetime import datetime
import json

import httpx
import pytest

from nepa.config import ConfigError, ModelPrice, load_config
from nepa.llm.client import LLMClient, ProviderError, TransportError
from nepa.llm.telemetry import calculate_cost, price_usage
from nepa.run_store import BudgetExhausted
from test_provider_capabilities import PLUS, FLASH, client_for, events, qwen_config, request, store_for


@pytest.mark.parametrize('model,count,rates', [
    (FLASH, 32000, (.2, .04, .8)), (FLASH, 32001, (.6, .12, 2.4)),
    (FLASH, 256000, (.6, .12, 2.4)), (FLASH, 256001, (1.2, .24, 4.8)),
    (FLASH, 1000000, (1.2, .24, 4.8)),
    (PLUS, 32000, (2, .4, 8)), (PLUS, 32001, (2, .4, 8)),
    (PLUS, 256000, (2, .4, 8)), (PLUS, 256001, (6, 1.2, 24)),
    (PLUS, 1000000, (6, 1.2, 24)),
])
@pytest.mark.parametrize('all_cached', [False, True])
def test_decimal_tiers_use_total_input_including_cache(model, count, rates, all_cached):
    price = qwen_config().pricing['qwen/' + model]
    miss, hit, output = rates
    costs = []
    for instant in ['2026-09-14T10:00:00+08:00', '2026-09-13T10:00:00+08:00']:
        measured = price_usage(price, count, 1000, cache_hit_tokens=count if all_cached else 0,
                               started_at=datetime.fromisoformat(instant).timestamp())
        assert measured['period'] == measured['time_basis'] == 'flat'
        assert measured['tier_basis'] == 'total_input_tokens'
        assert measured['rates_per_million_tokens'] == {'cache_miss_input': miss, 'cache_hit_input': hit, 'output': output}
        assert measured['cost_cny'] == pytest.approx((count * (hit if all_cached else miss) + 1000 * output) / 1e6)
        costs.append(measured['cost_cny'])
    assert costs[0] == costs[1]
    assert calculate_cost(price, count, 1000) >= costs[0]


def test_non_deepseek_prices_do_not_inherit_off_peak_discount():
    price = ModelPrice(input_cny_per_million_tokens=2, cache_hit_input_cny_per_million_tokens=.4,
                       output_cny_per_million_tokens=8)
    weekend = datetime.fromisoformat('2026-09-13T10:00:00+08:00').timestamp()
    assert price_usage(price, 1000000, 1000000, started_at=weekend)['cost_cny'] == 10
    assert price.schedule == 'flat'
    with pytest.raises(ConfigError, match='DeepSeek schedule'):
        qwen_config(pricing={'qwen/' + PLUS: {'schedule': 'deepseek', 'off_peak_multiplier': .5}})
    with pytest.raises(ConfigError, match='price'):
        load_config(overrides={'coder': {'provider': 'anthropic', 'model': 'unpriced'}})


@pytest.mark.parametrize('model', [PLUS, FLASH])
def test_pricing_rejects_out_of_profile_usage(model):
    price = qwen_config().pricing['qwen/' + model]
    with pytest.raises(ValueError):
        price_usage(price, 1000001, 1)
    with pytest.raises(ValueError):
        price_usage(price, 1, 1, cache_hit_tokens=2)


@pytest.mark.parametrize('code,attempts', [(400, 1), (401, 1), (402, 1), (408, 1), (429, 3), (500, 3), (503, 3)])
def test_http_failure_classes_each_attempt_reserves_and_retains(tmp_path, monkeypatch, code, attempts):
    config = qwen_config(campaign={'phase': 'public_tools'})
    seen = []
    def handler(req):
        seen.append(req.read())
        return httpx.Response(code, text='observed HTTP failure')
    client = client_for(config, handler)
    store = store_for(tmp_path, config)
    prepared = client.prepare(request())
    monkeypatch.setattr('nepa.llm.client.time.sleep', lambda _: None)
    with pytest.raises(ProviderError) as caught:
        client.complete(request(), store=store, task_id='probe')
    assert caught.value.failure_class == ('rate_limit' if code == 429 else 'server' if code >= 500 else 'http_request')
    assert len(seen) == store.run['budget']['calls'] == len(store.run['pending_calls']) == attempts
    assert all(body == prepared.body for body in seen)
    assert store.run['budget']['cost_cny'] == pytest.approx(attempts * prepared.reservation_cny)
    assert store.run['phase_cost_cny']['public_tools'] == pytest.approx(attempts * prepared.reservation_cny)


def test_partial_network_failure_preserves_observed_sse_and_reserves_retries(tmp_path, monkeypatch):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"model":"observed-partial"}\n\n'
            raise httpx.ReadError('lost transport')
    config = qwen_config()
    client = client_for(config, lambda _: httpx.Response(200, stream=BrokenStream()))
    store = store_for(tmp_path, config)
    monkeypatch.setattr('nepa.llm.client.time.sleep', lambda _: None)
    with pytest.raises(TransportError) as caught:
        client.complete(request(), store=store, task_id='probe')
    assert caught.value.failure_class == 'transport'
    assert len(store.run['pending_calls']) == 3
    for path in (store.root / 'evidence/calls').glob('*.fault-response.json'):
        assert 'observed-partial' in json.loads(path.read_text())['sse']


def test_known_liability_over_reservation_is_never_clipped_and_blocks_next_call(tmp_path):
    config = qwen_config()
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, text=events(usage={'prompt_tokens': 256001, 'completion_tokens': 100000}))
    client = client_for(config, handler)
    store = store_for(tmp_path, config)
    prepared = client.prepare(request(max_tokens=1))
    response = client.complete(request(max_tokens=1), prepared=prepared, store=store, task_id='probe')
    actual = (256001 * 6 + 100000 * 24) / 1e6
    assert actual > prepared.reservation_cny
    assert response.cost_cny == store.run['budget']['cost_cny'] == pytest.approx(actual)
    assert store.run['liability_overflow_cny'] == pytest.approx(actual - prepared.reservation_cny)
    assert not store.run['pending_calls']
    saved = json.loads((store.root / 'evidence/calls/000001.response.json').read_text())
    assert saved['response']['cost_cny'] == pytest.approx(actual)
    with pytest.raises(BudgetExhausted):
        client.complete(request(), store=store, task_id='probe')
    assert len(calls) == 1
