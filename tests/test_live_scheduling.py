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
    config = load_config(overrides={"budgets": {"max_cost_usd": 100, "campaign_max_cost_usd": 100}})
    stores = [RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/specIR.json",
                                 ROOT / "gold_file/target.json", ROOT / "gold_file/acceptance.json", config)
              for _ in range(2)]
    barrier = threading.Barrier(2)
    def reserve(store):
        barrier.wait(timeout=10)
        try:
            store.reserve_call("bootstrap", 60, {})
            return True
        except BudgetExhausted:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, stores))
    assert sorted(results) == [False, True]
    total = sum(json.loads((store.root / "run.json").read_bytes())["budget"]["cost_usd"] for store in stores)
    assert total == 60


@pytest.mark.parametrize("failed_index", [None, 1, 2])
@pytest.mark.parametrize("resumed_first", [False, True])
def test_scheduler_gates_repetitions_on_first_complete_success(tmp_path, monkeypatch, failed_index, resumed_first):
    spec = importlib.util.spec_from_file_location("live_scheduler_under_test", ROOT / "tests/test_live_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("NEPA_LIVE_E2E", "1")
    monkeypatch.setenv("NEPA_DS_API_KEY", "test-only-no-api")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_config", lambda _: load_config())
    current_frozen = {"test_only": True, "inputs": {}, "image": "test-image"}
    baseline_frozen = {"test_only_baseline": True, "inputs": {}, "image": "test-image"}
    monkeypatch.setattr(module, "fingerprint", lambda _: current_frozen)
    monkeypatch.setattr(module, "git", lambda *args: "")
    if resumed_first:
        from types import SimpleNamespace
        state = {"status": "failed" if failed_index == 1 else "success",
                 "exit_code": 2 if failed_index == 1 else 0, "history": []}
        monkeypatch.setattr(module.RunStore, "open", lambda *args: SimpleNamespace(root=tmp_path, run=state, config=load_config()))
        evidence = tmp_path / "first-evidence.json"
        evidence.write_text(json.dumps({"status": "passed", "frozen": baseline_frozen,
                                        "runs": [{"run_id": "test-only-resumed-first"}]}))
    barrier = threading.Barrier(2)
    first_verified = threading.Event()
    started = []
    def launch(index, runs_root):
        started.append(index)
        if index != 1:
            assert first_verified.is_set()
            barrier.wait(timeout=10)
        return {"index": index, "returncode": 2 if index == failed_index else 0}
    def verify(row, config, frozen, batch):
        assert row["returncode"] == 0
        assert frozen == (baseline_frozen if resumed_first and row["index"] == 1 else current_frozen)
        if row["index"] == 1:
            first_verified.set()
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(module, "verify_row", verify)
    result = module.run_batch(evidence if resumed_first else None)
    expected = ([1] if failed_index == 1 else [1, 2, 3])
    if resumed_first:
        expected.remove(1)
    assert sorted(started) == expected
    assert len(result["runs"]) == (1 if failed_index == 1 else 3)
    assert result["status"] == ("passed" if failed_index is None else "failed")
    assert sum(row["passed"] for row in result["runs"]) == (0 if failed_index == 1 else 2 if failed_index == 2 else 3)
