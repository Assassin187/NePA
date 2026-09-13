"""Scheduler/concurrent-accounting tests only; never paid generation evidence."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from nepa.config import load_config
from nepa.run_store import BudgetExhausted, RunStore

ROOT = Path(__file__).parents[1]


def test_parallel_reservations_cannot_overrun_shared_campaign(tmp_path):
    config = load_config(overrides={"budgets": {"max_cost_cny": 20, "campaign_max_cost_cny": 20}})
    stores = [RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                 ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", config)
              for _ in range(2)]
    barrier = threading.Barrier(2)
    def reserve(store):
        barrier.wait(timeout=10)
        try:
            store.reserve_call("bootstrap", 12, {})
            return True
        except BudgetExhausted:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, stores))
    assert sorted(results) == [False, True]
    total = sum(json.loads((store.root / "run.json").read_bytes())["budget"]["cost_cny"] for store in stores)
    assert total == 12


@pytest.mark.parametrize("failed_protocol", [None, "mqtt", "http"])
def test_scheduler_runs_protocols_concurrently_and_retains_both_results(tmp_path, monkeypatch, failed_protocol):
    spec = importlib.util.spec_from_file_location("live_scheduler_under_test", ROOT / "tests/test_live_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("NEPA_LIVE_E2E", "1")
    monkeypatch.setenv("NEPA_DS_API_KEY", "test-only-no-api")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_config", lambda _: load_config())
    frozen = {"test_only": True}
    monkeypatch.setattr(module, "fingerprint", lambda _: frozen)
    monkeypatch.setattr(module, "git", lambda *args: "")
    started, verified = [], []
    barrier = threading.Barrier(2)
    roots = module.campaign_roots()
    def launch(protocol, runs_root):
        assert runs_root == roots[protocol]
        started.append(protocol)
        barrier.wait(timeout=10)
        return {"protocol": protocol, "returncode": 2 if protocol == failed_protocol else 0}
    def verify(row, config, current_frozen, batch):
        assert row["returncode"] == 0 and current_frozen == frozen
        verified.append(row["protocol"])
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(module, "verify_row", verify)
    result = module.run_batch()
    assert sorted(started) == ["http", "mqtt"]
    assert len(result["runs"]) == 2
    assert sorted(verified) == sorted(p for p in started if p != failed_protocol)
    assert result["status"] == ("passed" if failed_protocol is None else "failed")
    assert roots["mqtt"] != roots["http"]


def test_http_campaign_reservation_does_not_reset_or_charge_mqtt(tmp_path):
    config = load_config(overrides={"budgets": {"max_cost_cny": 20, "campaign_max_cost_cny": 20}})
    stores = [RunStore.initialize(tmp_path / name, ROOT / "gold_file/mqtt/specIR.json",
                                 ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", config)
              for name in ("mqtt", "http")]
    stores[0].reserve_call("bootstrap", 18, {})
    stores[1].reserve_call("bootstrap", 18, {})
    for store in stores:
        assert store.run["budget"]["cost_cny"] == 18
        with pytest.raises(BudgetExhausted):
            store.reserve_call("bootstrap", 3, {})
