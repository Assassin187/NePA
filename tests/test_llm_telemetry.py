import hashlib
import json

import httpx
import pytest

from nepa.config import load_config
from nepa.llm.client import LLMCallContext, LLMClient, LLMRequest, LLMResponse, ProviderError, ParameterSupportState
from nepa.orchestrator import CrashInjected
from nepa.llm.telemetry import LLMTelemetry
from nepa.run_store import RunStore


def _config():
    return load_config(
        overrides={
            "providers": {"fixture": {"kind": "openai_compat", "base_url": "https://fixture", "api_key_env": "FIXTURE_KEY"}},
            "pricing": {"models": {"fixture/model": {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}}},
        }
    )


def _request(user="user"):
    return LLMRequest(role="fixture_role", system="system", user=user, temperature=0.1, max_tokens=32)


class _Provider:
    native_structured_output = False

    def __init__(self, calls, *, text="answer", failure=None):
        self.calls = calls
        self.text = text
        self.failure = failure

    def complete(self, request, *, model, native_schema):
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return LLMResponse(
            text=self.text,
            tokens_in=2,
            tokens_out=3,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


def _client(tmp_path, provider, *, secret="telemetry-secret"):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    telemetry = LLMTelemetry(store, secret_values={secret})
    return LLMClient(_config(), {"fixture": provider}, store=store, telemetry=telemetry), store


def _context():
    return LLMCallContext(
        run_id="run-1",
        stage="s6",
        tier="T2",
        task_id="T-1",
        attempt=2,
        trace_fields={"compiler_phase": "prepare", "ignored": "drop"},
    )


def test_success_trace_publishes_prompt_output_hash_and_optional_fields(tmp_path):
    calls = []
    client, store = _client(tmp_path, _Provider(calls, text="answer telemetry-secret"))
    response = client.complete(_request("user telemetry-secret"), provider_name="fixture", model="model", context=_context())
    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])
    prompt = (store.root / row["prompt_path"]).read_bytes()
    output = (store.root / row["output_path"]).read_text(encoding="utf-8")

    assert response.text == "answer telemetry-secret"
    assert row["run_id"] == "run-1"
    assert row["stage"] == "s6"
    assert row["agent_role"] == "fixture_role"
    assert row["compiler_phase"] == "prepare"
    assert row["prompt_sha256"] == hashlib.sha256(prompt).hexdigest()
    assert "telemetry-secret" not in prompt.decode("utf-8")
    assert "telemetry-secret" not in output
    for path in row["provider_prompt_paths"] + row["provider_output_paths"]:
        assert (store.root / path).exists()


def test_cache_hit_has_one_logical_trace_row_per_call_and_zero_incremental_cost(tmp_path):
    calls = []
    client, store = _client(tmp_path, _Provider(calls))
    request = _request()
    client.complete(request, provider_name="fixture", model="model", context=_context())
    hit = client.complete(request, provider_name="fixture", model="model", context=_context())
    rows = [json.loads(line) for line in (store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()]

    assert hit.cached is True
    assert len(calls) == 1
    assert len(rows) == 2
    assert rows[1]["cached"] is True
    assert rows[1]["cost_usd"] == 0


def test_provider_failure_trace_is_sanitized_and_has_no_missing_output_reference(tmp_path):
    calls = []
    client, store = _client(
        tmp_path,
        _Provider(calls, failure=ProviderError("provider failure telemetry-secret", provider="fixture", status_code=400)),
    )

    with pytest.raises(ProviderError):
        client.complete(_request(), provider_name="fixture", model="model", context=_context())
    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])
    output_path = store.root / row["output_path"]

    assert row["validation"] == "fail"
    assert output_path.exists()
    assert "telemetry-secret" not in output_path.read_text(encoding="utf-8")
    assert row["error"] == "provider failure [REDACTED]"


@pytest.mark.parametrize(
    ("fault_point", "row_expected"),
    [
        ("llm_prompt_before_publish", False),
        ("llm_prompt_published", False),
        ("llm_output_before_publish", False),
        ("llm_output_published", False),
        ("llm_trace_before_append", False),
        ("llm_trace_appended", True),
    ],
)
def test_trace_crash_windows_leave_only_valid_committed_rows_and_replay_from_fresh_instances(tmp_path, fault_point, row_expected):
    calls = []

    def crash(point):
        if point == fault_point:
            raise CrashInjected()

    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    telemetry = LLMTelemetry(store, fault_hook=crash)
    first = LLMClient(_config(), {"fixture": _Provider(calls)}, store=store, telemetry=telemetry)
    with pytest.raises(CrashInjected):
        first.complete(_request(), provider_name="fixture", model="model", context=_context(), use_cache=False)
    trace_path = store.root / "trace/llm_calls.ndjson"
    rows = trace_path.read_text(encoding="utf-8").splitlines() if trace_path.exists() else []
    assert bool(rows) is row_expected
    if row_expected:
        row = json.loads(rows[0])
        assert (store.root / row["prompt_path"]).exists()
        assert (store.root / row["output_path"]).exists()
    else:
        resumed_calls = []
        resumed = LLMClient(
            _config(),
            {"fixture": _Provider(resumed_calls)},
            store=RunStore(store.root),
            telemetry=LLMTelemetry(RunStore(store.root)),
        )
        resumed.complete(_request(), provider_name="fixture", model="model", context=_context(), use_cache=False)
        all_rows = (store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()
        assert len(all_rows) == 1
        assert resumed_calls
        recovered = json.loads(all_rows[0])
        for path in recovered["provider_prompt_paths"] + recovered["provider_output_paths"]:
            assert (store.root / path).exists()


class _SequenceProvider:
    native_structured_output = False

    def __init__(self, texts):
        self.texts = list(texts)

    def complete(self, request, *, model, native_schema):
        return LLMResponse(
            text=self.texts.pop(0),
            tokens_in=3,
            tokens_out=4,
            cost_usd=0,
            model=model,
            parameter_support={"temperature": ParameterSupportState.UNKNOWN},
        )


def test_repair_transport_failure_retains_first_provider_output(tmp_path):
    class Provider:
        native_structured_output = False

        def __init__(self):
            self.calls = 0

        def complete(self, request, *, model, native_schema):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    text='{"answer":3}',
                    tokens_in=3,
                    tokens_out=4,
                    cost_usd=0,
                    model=model,
                    parameter_support={"temperature": ParameterSupportState.UNKNOWN},
                )
            raise ProviderError("repair transport failed", provider="fixture", status_code=400)

    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    client = LLMClient(
        _config(),
        {"fixture": Provider()},
        store=store,
        telemetry=LLMTelemetry(store),
    )
    with pytest.raises(ProviderError):
        client.complete(_structured_request(), provider_name="fixture", model="model", context=_context())

    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])
    first_output = store.root / row["provider_output_paths"][0]
    assert first_output.exists()
    assert '"text":"{\\"answer\\":3}"' in first_output.read_text(encoding="utf-8")
    assert (store.root / row["output_path"]).exists()
    assert row["tokens_in"] == 3
    assert row["tokens_out"] == 4
    assert row["cost_usd"] == pytest.approx(11 / 1_000_000)
    assert row["repair_attempts"] == 1


def _structured_request():
    return LLMRequest(
        role="fixture_role",
        system="system",
        user="user",
        json_schema={"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
        temperature=0,
        max_tokens=32,
    )


def test_repaired_transcript_preserves_both_provider_prompts_outputs_and_aggregate_usage(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    client = LLMClient(
        _config(),
        {"fixture": _SequenceProvider(['{"answer":3}', '{"answer":"fixed"}'])},
        store=store,
        telemetry=LLMTelemetry(store),
    )
    response = client.complete(_structured_request(), provider_name="fixture", model="model", context=_context())
    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])

    assert response.validation.value == "repaired"
    assert row["validation"] == "repaired"
    assert row["tokens_in"] == 6
    assert row["tokens_out"] == 8
    assert len(row["provider_prompt_paths"]) == 2
    assert len(row["provider_output_paths"]) == 2
    assert all((store.root / path).exists() for path in row["provider_prompt_paths"] + row["provider_output_paths"])


def test_final_invalid_transcript_is_committed_as_fail_before_typed_error(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    client = LLMClient(
        _config(),
        {"fixture": _SequenceProvider(['{"answer":3}', '{"answer":4}'])},
        store=store,
        telemetry=LLMTelemetry(store),
    )
    with pytest.raises(Exception, match="structured output"):
        client.complete(_structured_request(), provider_name="fixture", model="model", context=_context())
    row = json.loads((store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8").splitlines()[0])

    assert row["validation"] == "fail"
    assert row["tokens_in"] == 6
    assert row["tokens_out"] == 8
    assert row["cost_usd"] == pytest.approx(22 / 1_000_000)
    assert len(row["provider_output_paths"]) == 2
    assert all((store.root / path).exists() for path in row["provider_prompt_paths"] + row["provider_output_paths"])
