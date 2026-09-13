"""Offline session/caller boundary regressions; these are not live model evidence."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nepa.agents.context import CodingContext
from nepa.application import build_orchestrator
from nepa.config import load_config
from nepa.llm.client import LLMResponse
from nepa.orchestrator import Orchestrator
from nepa.run_store import RunStore, RunStoreError
from test_agent_session import SequenceProvider, action

ROOT = Path(__file__).parents[1]
PRIVATE = "/host/private/assets/hidden-oracle.py SECRET_SEED PRIVATE_VECTOR"


def failed_verification(count=1):
    return {"passed": False, "seed": PRIVATE, "evidence": {"path": PRIVATE, "sha256": "a" * 64},
            "variants": [{"variant": "release", "passed": False,
                          "execution": {"stdout": PRIVATE, "stderr": PRIVATE, "command": [PRIVATE]},
                          "detail": {"passed": False, "server_stdout": PRIVATE,
                                     "checks": [{"id": f"check-{i}", "category": "response_mismatch", "passed": False,
                                                 "argv": [PRIVATE], "stdout": PRIVATE,
                                                 "observation": {"expected": {"type": "ack", "length": 2},
                                                                 "observed": {"type": "reply", "length": 3},
                                                                 "seed": PRIVATE}} for i in range(count)]}}]}


def initial(request):
    return json.loads(request.messages[0]["content"].split("\nDecision budget:")[0])


def native(value, identifier):
    return LLMResponse(text="", reasoning_content="retained native reasoning", tokens_in=10, tokens_out=10,
                       cost_cny=0, model="deepseek-v4-pro", parameter_support={},
                       tool_calls=[{"id": identifier, "type": "function", "function": {
                           "name": value["tool"], "arguments": json.dumps(value["arguments"])}}],
                       provider_metadata={"finish_reason": "tool_calls", "returned_model_identity_observed": True,
                                          "returned_model_identity": "deepseek-v4-pro",
                                          "usage": {"prompt_tokens": 10, "completion_tokens": 10}})


def make_session(tmp_path, actions, *, action_format="json_object", sessions=2, decisions=10, fast=None):
    config = load_config(overrides={"coder": {"action_format": action_format, "fast_model": fast},
                                    "budgets": {"sessions_per_task": sessions, "decisions_per_session": decisions}})
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", config)
    provider = SequenceProvider(actions, config)
    session = build_orchestrator(store, {"deepseek": provider}).session
    session.builder = SimpleNamespace(run=lambda *args, **kwargs: {"passed": True, "builds": []})
    return store, session, provider


@pytest.mark.parametrize("action_format", ["json_object", "tool_calls"])
def test_failed_finish_publishes_safe_paginated_feedback_and_accepts_raw_evidence(tmp_path, action_format):
    def page(request):
        receipt = json.loads(request.messages[-1]["content"].split("\nDecision budget:")[0])
        return action("read_file", path=receipt["evidence_ref"]["path"], offset=0, limit=200)

    actions = [action("finish", summary="check", claims=[]), page,
               lambda request: action("read_file", path=initial(request)["current_observations"][0]["read"]["path"],
                                      offset=200, limit=200),
               action("finish", summary="repair", claims=[])]
    if action_format == "tool_calls":
        actions = [(lambda request, value=value, i=i: native(value(request) if callable(value) else value, f"call-{i}"))
                   for i, value in enumerate(actions)]
    store, session, provider = make_session(tmp_path, actions, action_format=action_format)
    results = iter([failed_verification(150), {"passed": True, "variants": []}])
    roots = []
    def verify(target, acceptance, project, checks, evidence):
        roots.append(checks)
        return next(results)
    session.verifier = SimpleNamespace(run=verify)
    task = store.plan()["tasks"][-1]
    assert session.run(task)
    assert roots == [store.private_checks, store.private_checks]
    assert store.run["tasks"][task["id"]]["decisions"] == 4
    for request in provider.requests:
        wire = json.dumps(request.model_dump())
        assert PRIVATE not in wire and "SECRET_SEED" not in wire and "PRIVATE_VECTOR" not in wire
        for message in request.messages:
            if message["role"] in {"tool", "user"}:
                json.loads(message["content"].split("\nDecision budget:")[0])
    request = provider.requests[-1]
    observations = initial(request)["current_observations"]
    assert [row["read"]["offset"] for row in observations] == [0, 200]
    assert all(row["read"]["path"].startswith("evidence/actions/") for row in observations)
    for row in observations:
        assert row["result"]["file_sha256"] == session.tools.file_sha256(row["read"]["path"])
    diagnostic = initial(request)["latest_observed_diagnostic"]
    assert "complete_result_ref" in diagnostic["tool_result"]
    projected = store.read_agent_evidence(diagnostic["evidence_ref"])
    assert projected["verification"]["variants"][0]["detail"]["checks"][0]["observation"]["observed"]["length"] == 3
    raw = store.read_ref(diagnostic["evidence_ref"] | {
        "sha256": hashlib.sha256((store.root / diagnostic["evidence_ref"]["path"]).read_bytes()).hexdigest()})
    assert raw["result"]["verification"]["seed"] == PRIVATE
    accepted = store.run["tasks"][task["id"]]["evidence"][-1]
    assert store.read_ref(accepted)["result"]["accepted"] is True
    assert accepted["sha256"] != store.run["tasks"][task["id"]]["last_feedback"]["evidence_ref"]["sha256"]
    if action_format == "tool_calls":
        for assistant, receipt in zip(request.messages[1::2], request.messages[2::2]):
            assert assistant["reasoning_content"] == "retained native reasoning"
            assert receipt["tool_call_id"] == assistant["tool_calls"][0]["id"]


@pytest.mark.parametrize("action_format", ["json_object", "tool_calls"])
def test_resume_restores_last_diagnostic_from_safe_root_after_later_read_and_eviction(tmp_path, action_format):
    actions = [action("finish", summary="check", claims=[]), action("read_file", path="inputs/target.json")]
    if action_format == "tool_calls":
        actions = [native(value, str(i)) for i, value in enumerate(actions)]
    store, session, provider = make_session(tmp_path, actions, action_format=action_format, sessions=1, decisions=2)
    session.verifier = SimpleNamespace(run=lambda *args, **kwargs: failed_verification())
    task = store.plan()["tasks"][-1]
    assert not session.run(task)
    saved = store.run["tasks"][task["id"]]["last_feedback"]
    assert "read" in saved["tool_result"]
    assert saved["latest_observed_diagnostic"]["tool_result"]["verification"]["passed"] is False
    # Retry with a fresh session object, as after process restart. Private/action
    # host reads are forbidden; public facts/plan retain their normal host readers.
    reopened = RunStore.open(store.root.parent, store.run_id)
    read_ref = reopened.read_ref
    def public_inputs_only(ref):
        assert not ref["path"].startswith("evidence/")
        return read_ref(ref)
    reopened.read_ref = public_inputs_only
    values = [action("finish", summary="retry", claims=[])]
    if action_format == "tool_calls":
        values = [native(values[0], "retry")]
    retry_provider = SequenceProvider(values, reopened.config)
    resumed = build_orchestrator(reopened, {"deepseek": retry_provider}).session
    resumed.builder = session.builder
    resumed.verifier = SimpleNamespace(run=lambda *args, **kwargs: {"passed": True, "variants": []})
    assert resumed.run(task, repair=True)
    facts = initial(retry_provider.requests[0])
    assert facts["initial_feedback"]["tool_result"]["path"] == "inputs/target.json"
    assert "content" not in facts["initial_feedback"]["tool_result"]
    assert facts["latest_observed_diagnostic"]["tool_result"]["verification"]["passed"] is False
    assert "SECRET_SEED" not in json.dumps(facts)
    # Force complete-transaction eviction while keeping the restored diagnostic.
    context = CodingContext(session.system, {"task": task}, reopened.config.coder, resumed.tools,
                            session.schema, client=resumed.client)
    context.coder.context_max_bytes = 40000
    context.latest_diagnostic = facts["latest_observed_diagnostic"]
    for i in range(12):
        value = action("search", pattern="x" * 5000)
        context.record(native(value, f"evict-{i}") if action_format == "tool_calls" else json.dumps(value),
                       {"tool_result": {"matches": []}})
        request = context.request({})
    assert context.evicted_transactions > 0
    assert initial(request)["latest_observed_diagnostic"] == facts["latest_observed_diagnostic"]
    assert "SECRET_SEED" not in context.prepared_request.body.decode()


@pytest.mark.parametrize("stage", ["build", "verification", "workspace"])
def test_exception_message_stays_host_only_and_never_accepts_failed_verification(tmp_path, stage):
    first = action("read_file", path="missing") if stage == "workspace" else action("finish", summary="check", claims=[])
    store, session, provider = make_session(tmp_path, [first], sessions=1, decisions=1, fast="deepseek-v4-flash")
    def fail(*args, **kwargs):
        raise OSError(PRIVATE)
    task = store.plan()["tasks"][-1] if stage == "verification" else store.plan()["tasks"][0]
    if stage == "workspace":
        session.tools.execute = fail
    elif stage == "build":
        session.builder.run = fail
    else:
        session.verifier = SimpleNamespace(run=fail)
    assert not session.run(task)
    state = store.run["tasks"][task["id"]]
    assert not state["evidence"] and state["decisions"] == state["sessions"] == 1
    assert PRIVATE not in json.dumps(state["last_feedback"])
    host = list((store.root / "evidence/actions").glob("*.json"))
    assert PRIVATE in host[0].read_text()
    if stage != "workspace":
        assert state["last_feedback"]["model_route"]["reason"] == ("build_repair" if stage == "build" else "private_acceptance_repair")


def test_followup_uses_published_reference_and_rejects_valid_raw_hash(tmp_path):
    store, session, provider = make_session(tmp_path, [], sessions=2, decisions=1)
    public = session.publish_feedback("diagnostic.json", {"verification": failed_verification()})
    raw = store.evidence("host-only.json", {"seed": PRIVATE})
    provider.actions = iter([action("request_followup", issue="repair observed response", requirement_ids=[],
                                   diagnostic_refs=[raw]), action("finish", summary="done", claims=[])])
    task = store.plan()["tasks"][0]
    assert session.run(task)
    assert store.run["followups"] == 0
    assert PRIVATE not in json.dumps(store.run["tasks"][task["id"]]["last_feedback"])
    # A different, unexhausted ordinary task can schedule the same public diagnostic.
    next_task = store.plan()["tasks"][1]
    provider.actions = iter([action("request_followup", issue="repair observed response", requirement_ids=[],
                                   diagnostic_refs=[public["evidence_ref"]]), action("finish", summary="done", claims=[])])
    assert session.run(next_task)
    followup = store.plan()["tasks"][-2]
    assert followup["kind"] == "followup"
    assert followup["context"]["diagnostic_refs"] == [public["evidence_ref"]]
    assert store.read_agent_evidence(followup["context"]["diagnostic_refs"][0])["verification"]["passed"] is False
    with pytest.raises((OSError, RunStoreError)):
        session._restore_feedback({"evidence_ref": raw})


@pytest.mark.parametrize("failure", ["build", "verification"])
def test_export_repair_caller_passes_only_published_view(tmp_path, monkeypatch, failure):
    store, session, _ = make_session(tmp_path, [])
    engine = Orchestrator(session)
    monkeypatch.setattr("nepa.orchestrator.publish_report", lambda store: None)
    monkeypatch.setattr("nepa.orchestrator.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="image-id"))
    for state in store.run["tasks"].values():
        state["status"] = "passed"
    store.save()
    attempts = []
    def delivery(store, target, acceptance):
        attempts.append(True)
        store.run["final_checks"] = {"result": {"passed": False, "build": {"passed": failure != "build", "builds": []},
                                                 "verification": failed_verification() if failure == "verification" else None},
                                     "evidence": {"path": PRIVATE, "sha256": "a" * 64}}
        return len(attempts) > 1
    monkeypatch.setattr(engine, "_delivery", delivery)
    repairs = []
    def repair(task, *, repair, feedback):
        assert repair
        repairs.append(feedback)
        assert PRIVATE not in json.dumps(feedback)
        restored = session._restore_feedback(feedback)
        assert restored["tool_result"]["passed"] is False
        assert "evidence" not in restored["tool_result"]
        return True
    monkeypatch.setattr(session, "run", repair)
    assert engine.run(store) == 0
    assert store.run["final_repairs"] == len(repairs) == 1
    assert repairs[0]["model_route"]["reason"] == ("build_repair" if failure == "build" else "private_acceptance_repair")


@pytest.mark.parametrize("timed_out", [False, True])
def test_command_failure_or_timeout_routes_and_sends_the_same_measured_payload(tmp_path, timed_out):
    store, session, provider = make_session(tmp_path, [action("run_command", argv=["make"]),
                                                      action("finish", summary="repair", claims=[])],
                                             sessions=1, decisions=2, fast="deepseek-v4-flash")
    session.tools.execute = lambda *args: {"command": ["make"], "returncode": 0 if timed_out else 1,
                                          "timed_out": timed_out, "stdout": "", "stderr": "compile failed"}
    prepared = []
    prepare = session.client.prepare
    def measure(request):
        value = prepare(request)
        prepared.append(value)
        return value
    session.client.prepare = measure
    assert session.run(store.plan()["tasks"][0])
    assert len(prepared) == len(provider.sent) == 2
    assert all(sent is measured for sent, measured in zip(provider.sent, prepared))
    assert [row.model for row in prepared] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    progress = json.loads(provider.requests[1].messages[-1]["content"].split("\nDecision budget:")[1])
    assert progress["model_route"]["reason"] == "tool_failure_repair"
    assert progress["session"] == progress["decisions_left"] == 1
    diagnostic = initial(provider.requests[1])["latest_observed_diagnostic"]["tool_result"]
    assert diagnostic["timed_out"] == timed_out
    assert store.run["budget"]["calls"] == store.run["tasks"]["bootstrap"]["decisions"] == 2


def test_native_invalid_decisions_never_execute_and_keep_matched_receipts(tmp_path):
    edit = native(action("write_file", path="must-not-exist", content="invalid decision"), "invalid")
    values = [edit.model_copy(update={"tool_calls": []}),
              edit.model_copy(update={"tool_calls": [edit.tool_calls[0], {**edit.tool_calls[0], "id": "second"}]}),
              native(action("unknown"), "unknown"), native(action("write_file"), "invalid-args"),
              edit.model_copy(update={"provider_metadata": {**edit.provider_metadata, "finish_reason": "length"}}),
              native(action("finish", summary="only executed action", claims=[]), "finish")]
    store, session, provider = make_session(tmp_path, values, action_format="tool_calls", sessions=1, decisions=len(values))
    assert session.run(store.plan()["tasks"][0])
    assert not (store.project / "must-not-exist").exists()
    assert len(list((store.root / "evidence/actions").glob("*.json"))) == 1
    assert store.run["budget"]["calls"] == len(values)
    for request in provider.requests[1:]:
        assert "No tool executed" in request.messages[-1]["content"]
    for message in provider.requests[-1].messages:
        if message["role"] == "assistant":
            assert message["reasoning_content"] == "retained native reasoning"


@pytest.mark.parametrize("kind,retry,reason", [("bootstrap", False, "initial_ordinary_task"),
                                              ("shared-wire", False, "shared_interface"),
                                              ("integration", False, "integration"),
                                              ("followup", False, "task_retry"),
                                              ("bootstrap", True, "task_retry")])
def test_route_reasons_use_task_kind_and_retry_state(tmp_path, kind, retry, reason):
    store, session, provider = make_session(tmp_path, [action("finish", summary="done", claims=[])],
                                            fast="deepseek-v4-flash")
    task = {**store.plan()["tasks"][0], "kind": kind}
    if retry:
        store.run["tasks"][task["id"]]["sessions"] = 1
    session.verifier = SimpleNamespace(run=lambda *args, **kwargs: {"passed": True, "variants": []})
    assert session.run(task)
    progress = json.loads(provider.requests[0].messages[-1]["content"].split("\nDecision budget:")[1])
    assert progress["model_route"]["reason"] == reason
    assert progress["model_route"]["model"] == ("deepseek-v4-flash" if reason == "initial_ordinary_task" else "deepseek-v4-pro")


@pytest.mark.parametrize("action_format", ["json_object", "tool_calls"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_raw_initial_feedback_is_projected_before_first_request(tmp_path, action_format, wrapped):
    value = action("finish", summary="repair initial diagnostic", claims=[])
    if action_format == "tool_calls":
        value = native(value, "finish")
    store, session, provider = make_session(tmp_path, [value], action_format=action_format,
                                            fast="deepseek-v4-flash", sessions=1, decisions=1)
    raw = {"build": {"passed": True}, "verification": failed_verification()} if wrapped else failed_verification()
    assert session.run(store.plan()["tasks"][0], feedback=raw)
    request = provider.requests[0]
    assert PRIVATE not in json.dumps(request.model_dump())
    initial_feedback = initial(request)["initial_feedback"]
    assert initial_feedback["evidence_ref"]["path"].startswith("evidence/initial/")
    public = store.read_agent_evidence(initial_feedback["evidence_ref"])
    assert (public["verification"] if wrapped else public)["passed"] is False
    progress = json.loads(request.messages[-1]["content"].split("\nDecision budget:")[1])
    assert progress["model_route"]["reason"] == "private_acceptance_repair"
    assert request.model == "deepseek-v4-pro"
    assert store.run["tasks"]["bootstrap"]["sessions"] == store.run["budget"]["calls"] == 1


def test_initial_diagnostic_is_durable_when_no_valid_action_executes(tmp_path):
    store, session, provider = make_session(tmp_path, [""], sessions=1, decisions=1)
    task = store.plan()["tasks"][0]
    assert not session.run(task, feedback=failed_verification())
    reopened = RunStore.open(store.root.parent, store.run_id)
    saved = reopened.run["tasks"][task["id"]]["last_feedback"]
    assert PRIVATE not in json.dumps(saved)
    assert reopened.read_agent_evidence(saved["evidence_ref"])["passed"] is False
    assert saved["latest_observed_diagnostic"]["tool_result"]["passed"] is False
    assert saved["model_route"]["reason"] == "private_acceptance_repair"
    assert store.run["budget"]["calls"] == 1 and not (store.root / "evidence/actions").exists()
