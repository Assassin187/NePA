from datetime import datetime
import json

import pytest

from nepa.config import load_config
from nepa.llm.telemetry import calculate_cost, price_period, price_usage
from nepa.run_store import RunStoreError, campaign_cost_cny


@pytest.mark.parametrize('time,expected', [
    ('2026-09-14T08:59:59+08:00', 'off_peak'),
    ('2026-09-14T09:00:00+08:00', 'peak'),
    ('2026-09-14T11:59:59+08:00', 'peak'),
    ('2026-09-14T12:00:00+08:00', 'off_peak'),
    ('2026-09-14T13:59:59+08:00', 'off_peak'),
    ('2026-09-14T14:00:00+08:00', 'peak'),
    ('2026-09-14T17:59:59+08:00', 'peak'),
    ('2026-09-14T18:00:00+08:00', 'off_peak'),
    ('2026-09-12T10:00:00+08:00', 'off_peak'),
    ('2026-09-13T15:00:00+08:00', 'off_peak'),
    ('2026-09-14T01:00:00+00:00', 'peak'),
])
def test_domestic_period_boundaries_and_weekends(time, expected):
    assert price_period(datetime.fromisoformat(time).timestamp()) == expected


def test_domestic_cache_prices_reservations_and_missing_usage():
    config = load_config()
    peak = datetime.fromisoformat('2026-09-14T10:00:00+08:00').timestamp()
    off = datetime.fromisoformat('2026-09-13T10:00:00+08:00').timestamp()
    for model, hit, miss, output in [('deepseek-flash', .04, 2, 8), ('deepseek-v4-pro', .3, 9, 27)]:
        price = config.pricing['deepseek/' + model]
        assert calculate_cost(price, 1_000_000, 1_000_000) == miss + output
        p = price_usage(price, 1_000_000, 1_000_000, started_at=peak, cache_hit_tokens=500_000)
        assert p['cost_cny'] == .5 * hit + .5 * miss + output
        q = price_usage(price, 1_000_000, 1_000_000, started_at=off, cache_hit_tokens=500_000)
        assert q['cost_cny'] == p['cost_cny'] / 2
        assert price_usage(price, 10, 20, started_at=off)['cache_usage_basis'] == 'missing_assumed_all_miss'
        with pytest.raises(ValueError):
            price_usage(price, 10, 20, cache_hit_tokens=11)


def test_new_campaign_excludes_separate_old_usd_root_and_rejects_mixing(tmp_path):
    old = tmp_path / 'old-usd' / 'run'
    old.mkdir(parents=True)
    original = json.dumps({'schema_version': '5.0', 'budget': {'cost_usd': 88}})
    (old / 'run.json').write_text(original)
    new = tmp_path / 'new-cny'
    new.mkdir()
    assert campaign_cost_cny(new) == 0
    with pytest.raises(RunStoreError, match='new CNY campaign root'):
        campaign_cost_cny(old.parent)
    assert (old / 'run.json').read_text() == original
