from datetime import datetime, timezone
from pathlib import Path

from nepa.config import load_config
from nepa.orchestrator import BudgetExhausted, Orchestrator, StageResult, UsageDelta
from nepa.run_store import RunStore, SpecRunInputs


ROOT = Path(__file__).parents[1]


class FakeClock:
    def __init__(self):
        self.mono = 100.0
        self.utc = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def monotonic(self):
        return self.mono

    def utcnow(self):
        return self.utc


def _store(tmp_path, **budget):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s6"}, "budgets": budget}),
    )


def test_active_time_accumulates_across_sessions_and_offline_time_is_excluded(tmp_path):
    clock = FakeClock()
    store = _store(tmp_path, wall_clock_hours=1)
    first = Orchestrator(clock=clock)
    first._ensure_session(store)
    clock.mono += 4
    first._sync_budget(store, enforce=False)
    used = store.load_run()["budget_used"]["wall_clock_s"]

    clock.mono += 1000
    second = Orchestrator(clock=clock)
    second._ensure_session(store)
    clock.mono += 3
    second._sync_budget(store, enforce=False)

    assert used == 4
    assert store.load_run()["budget_used"]["wall_clock_s"] == 7


def test_pre_stage_exhaustion_leaves_stage_pending_and_runs_s9(tmp_path):
    store = _store(tmp_path, max_cost_usd=0)
    calls = []
    controller = Orchestrator({"s4": lambda context: calls.append("s4")}, monotonic=lambda: 1)

    assert controller.run_spec(store) == 10
    run = store.load_run()
    assert calls == []
    assert run["stages"]["s4"]["status"] == "pending"
    assert run["termination_request"]["stage"] == "s4"
    assert run["stages"]["s9"]["status"] == "done"


def test_post_call_usage_is_persisted_before_controlled_exit_and_cache_is_free(tmp_path):
    clock = FakeClock()
    store = _store(tmp_path, max_cost_usd=1)

    class UsageController:
        def run(self, context):
            context.orchestrator.admit_external_call(context.store)
            clock.mono += 2
            ref = context.store.publish_immutable_bytes("receipts/s4-usage.json", b"s4")
            return StageResult(output_refs={"receipt": ref}, usage=UsageDelta(tokens_in=4, tokens_out=5, cost_usd=1))

    controller = Orchestrator({"s4": UsageController()}, clock=clock)
    assert controller.run_spec(store) == 10
    used = store.load_run()["budget_used"]
    assert used["tokens_in"] == 4
    assert used["tokens_out"] == 5
    assert used["cost_usd"] == 1

    cache_store = _store(tmp_path / "cache", max_cost_usd=1)
    cache = Orchestrator(clock=clock)
    cache._ensure_session(cache_store)
    cache.record_external_usage(cache_store, UsageDelta(tokens_in=4, tokens_out=5, cost_usd=1, cached=True))
    assert cache_store.load_run()["budget_used"]["cost_usd"] == 0
