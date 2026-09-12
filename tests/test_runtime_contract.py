import json
from pathlib import Path
import pytest
from nepa.config import load_config, ConfigError, public_config_snapshot
from nepa.llm.client import LLMClient, LLMRequest, ProviderError, extract_first_json_value
from nepa.llm.telemetry import calculate_cost
from nepa.run_store import RunStore
from nepa.report import publish_report
from nepa.llm.client import structured_validation_errors
from nepa.schemas import load_schema

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

def test_authorized_cost_ceilings_do_not_change_time_budget():
    config = load_config(ROOT / "configs/default.yaml")
    assert config.budgets.max_cost_usd == 100
    assert config.budgets.campaign_max_cost_usd == 300
    assert config.budgets.wall_clock_hours == 4
    assert config.coder.json_output
    for key, limit in (("max_cost_usd", 100), ("campaign_max_cost_usd", 300)):
        with pytest.raises(ConfigError):
            load_config(overrides={"budgets": {key: limit + 1}})

@pytest.mark.parametrize("text", ['{"a":1}', '\x60\x60\x60json\n{"a":1}\n\x60\x60\x60', 'answer: {"a":1}'])
def test_json_envelope_parsing(text):
    assert extract_first_json_value(text) == {"a": 1}

def test_action_error_identifies_missing_argument_not_generic_schema_dump():
    errors = structured_validation_errors({"tool": "finish", "arguments": {"summary": "ready"}},
                                          load_schema("agent-action.schema.json"))
    assert errors == [{"path": ["arguments"], "message": "'claims' is a required property"}]

def test_pricing_known_and_negative():
    config = load_config()
    price = config.pricing["deepseek/deepseek-v4-pro"]
    assert calculate_cost(price, 1_000_000, 1_000_000) == pytest.approx(5.28)
    with pytest.raises(ValueError):
        calculate_cost(price, -1, 0)

@pytest.mark.parametrize("kind", ["bootstrap", "message", "requirements", "shared-wire", "final-integration", "followup"])
def test_fast_initial_coding_and_pro_complexity_escalation(kind):
    config = load_config(ROOT / "configs/default.yaml")
    coder = config.coder
    assert coder.fast_model == "deepseek-flash"
    expected = coder.fast_model if kind in {"bootstrap", "message", "requirements"} else coder.model
    assert coder.for_task(kind).model == expected
    assert coder.for_task(kind, retry=True).model == coder.model
    assert coder.for_task(kind, repair=True).model == coder.model
    assert coder.model == "deepseek-v4-pro"

def test_selected_model_controls_wire_and_cost(tmp_path):
    from nepa.llm.client import LLMResponse
    class Provider:
        native_structured_output = False
        def complete(self, request, *, model, native_schema):
            assert model == request.model == "deepseek-flash"
            return LLMResponse(text="{}", tokens_in=100, tokens_out=200, cost_usd=0,
                               model=model, parameter_support={})
    config = load_config(ROOT / "configs/default.yaml")
    store = make_store(tmp_path, config)
    response = LLMClient(config, {"deepseek": Provider()}).complete(
        LLMRequest(role="coder", model="deepseek-flash", json_output=True, system="s", user="u", temperature=0, max_tokens=1000),
        store=store, task_id="bootstrap")
    assert response.cost_usd == pytest.approx((100 * .30 + 200 * 1.20) / 1_000_000)
    evidence = json.loads((store.root / "evidence/calls/000001.request.json").read_text())
    assert "deepseek-flash" in json.dumps(evidence)
    assert evidence["wire"]["response_format"] == {"type": "json_object"}
    with pytest.raises(ConfigError, match="fast coder price"):
        load_config(overrides={"coder": {"fast_model": "unpriced"}})

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

def test_provider_payment_rejection_is_not_retried_or_accounted_as_free(tmp_path):
    class PaymentRejected:
        native_structured_output = False
        def __init__(self):
            self.calls = 0
        def complete(self, request, **kwargs):
            self.calls += 1
            raise ProviderError("deepseek returned HTTP 402", provider="deepseek", status_code=402)
    provider = PaymentRejected()
    store = make_store(tmp_path)
    client = LLMClient(store.config, {"deepseek": provider})
    with pytest.raises(ProviderError, match="402"):
        client.complete(LLMRequest(role="coder", system="s", user="u", temperature=0, max_tokens=1), store=store, task_id="bootstrap")
    assert provider.calls == store.run["budget"]["calls"] == 1
    assert store.run["budget"]["cost_usd"] > 0
    assert len(store.run["pending_calls"]) == 1

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

def test_failed_final_task_cannot_be_bypassed_by_later_check_success(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from nepa.orchestrator import Orchestrator
    store = make_store(tmp_path)
    for task in store.run["tasks"].values():
        task["status"] = "passed"
    class Session:
        def run(self, task, **kwargs):
            store.run["tasks"][task["id"]]["status"] = "failed"
            return False
    orchestrator = Orchestrator(Session())
    count = 0
    def delivery(*args):
        nonlocal count
        count += 1
        store.run['final_checks'] = {'result': {'passed': count > 1}}
        return count > 1
    monkeypatch.setattr(orchestrator, "_delivery", delivery)
    monkeypatch.setattr("nepa.orchestrator.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="image"))
    assert orchestrator.run(store) != 0
    assert store.run["status"] != "success"

def test_default_pytest_discovery_does_not_import_generated_project_scripts(tmp_path):
    import shutil
    import subprocess
    import sys
    shutil.copyfile(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_owned.py").write_text("def test_owned(): assert True\n")
    generated = tmp_path / "runs/example/delivery/tools"
    generated.mkdir(parents=True)
    (generated / "test_generated.py").write_text("raise RuntimeError('generated scripts belong in the sandbox')\n")
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_owned.py::test_owned" in result.stdout
    assert "test_generated" not in result.stdout
