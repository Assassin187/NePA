import json
from pathlib import Path
import pytest
from nepa.config import load_config, ConfigError, public_config_snapshot
from nepa.llm.client import LLMClient, LLMRequest, LLMResponse, ProviderError, extract_first_json_value
from nepa.llm.telemetry import calculate_cost
from nepa.run_store import RunStore
from nepa.report import publish_report

ROOT = Path(__file__).parents[1]

def make_store(tmp_path, config=None):
    return RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/specIR.json",
                               ROOT / "gold_file/target.json", ROOT / "gold_file/acceptance.json", config or load_config())

def test_config_override_and_secret_free_snapshot(monkeypatch):
    monkeypatch.setenv("NEPA_DS_API_KEY", "secret-value")
    config = load_config(overrides={"coder": {"max_tokens": 2000}})
    assert config.coder.max_tokens == 2000
    assert "secret-value" not in json.dumps(public_config_snapshot(config))
    with pytest.raises(ConfigError):
        load_config(overrides={"budgets": {"max_cost_usd": 1000}})
    with pytest.raises(ConfigError):
        load_config(overrides={"calibration_models": {}})

@pytest.mark.parametrize("text", ['{"a":1}', '\x60\x60\x60json\n{"a":1}\n\x60\x60\x60', 'answer: {"a":1}'])
def test_json_envelope_parsing(text):
    assert extract_first_json_value(text) == {"a": 1}

def test_pricing_known_and_negative():
    config = load_config()
    price = config.pricing["deepseek/deepseek-v4-pro"]
    assert calculate_cost(price, 1_000_000, 1_000_000) == pytest.approx(5.28)
    with pytest.raises(ValueError):
        calculate_cost(price, -1, 0)

def test_provider_retry_is_bounded_and_all_attempts_accounted(tmp_path, monkeypatch):
    class FailingProvider:
        native_structured_output = False
        def __init__(self):
            self.calls = 0
        def complete(self, request, *, model, native_schema):
            self.calls += 1
            raise ProviderError("service unavailable", provider="deepseek", status_code=503)
    store = make_store(tmp_path)
    provider = FailingProvider()
    monkeypatch.setattr("nepa.llm.client.time.sleep", lambda _: None)
    client = LLMClient(store.config, {"deepseek": provider})
    with pytest.raises(ProviderError):
        client.complete(LLMRequest(role="coder", system="s", user="u", temperature=0, max_tokens=1), store=store, task_id="bootstrap")
    assert provider.calls == 3
    assert store.run["budget"]["calls"] == 3
    assert len(store.run["pending_calls"]) == 3
    assert store.run["budget"]["cost_usd"] > 0

def test_failed_run_with_input_drift_still_reports_truthfully(tmp_path):
    store = make_store(tmp_path)
    (store.root / "inputs/spec.json").write_text("{}")
    store.run.update(status="invalid", exit_code=20, reason="input snapshot drift")
    report = publish_report(store)
    assert report["status"] == "invalid"
    assert report["input_verification_error"]
    assert report["requirements"] == []

def test_time_budget_not_reset_on_resume(tmp_path):
    from nepa.run_store import BudgetExhausted
    store = make_store(tmp_path)
    store.run["created_at"] = 0
    store.save()
    with pytest.raises(BudgetExhausted):
        RunStore(store.root).check_budget()
