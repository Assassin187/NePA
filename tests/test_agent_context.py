import json
from pathlib import Path

import pytest

from nepa.agents.context import CodingContext
from nepa.config import CoderConfig, load_config
from nepa.llm.client import LLMClient, LLMRequestError
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.workspace import WorkspaceTools


@pytest.fixture
def context(tmp_path):
    for name in ("project", "inputs", "evidence"):
        (tmp_path / name).mkdir()
    tools = WorkspaceTools(tmp_path / "project", tmp_path / "inputs", tmp_path / "evidence",
                           SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    config = load_config(overrides={"coder": {"context_max_bytes": 50000}})
    return CodingContext("one action", {"task": {"id": "generic", "requirements": ["all facts"]}},
                         config.coder, tools, client=LLMClient(config))


def read(context, path):
    action = {"tool": "read_file", "arguments": {"path": path}}
    result = context.tools.execute("read_file", action["arguments"])
    context.record(json.dumps(action), {"tool_result": result, "evidence_ref": {"path": "test-only", "sha256": "test-only"}}, action=action)


def initial(request):
    return json.loads(request.messages[0]["content"].split("\nDecision budget:")[0])


def test_required_source_working_set_survives_long_transcript_and_three_sessions(context):
    sources = {f"module-{n}.c": f"/* module {n} */" + "x" * 4000 for n in range(8)}
    for path, content in sources.items():
        (context.tools.project / path).write_text(content)
        read(context, path)
    for session in (1, 2, 3):
        for step in range(15):
            # Large historical actions force real transaction eviction.
            context.record(json.dumps({"tool": "run_command", "arguments": {"argv": ["echo", "x" * 5000]}}),
                           {"tool_result": {"returncode": 0}})
            read(context, "module-0.c")
            request = context.request({"session": session, "decisions_left": 40 - step})
            assert context.prepared_request.wire_bytes <= 50000
            assert {r["read"]["path"]: r["result"]["content"] for r in initial(request)["current_observations"]} == sources
            assert [m["role"] for m in request.messages] == ["user"] + ["assistant", "user"] * ((len(request.messages) - 1) // 2)
    assert context.evicted_transactions > 0
    assert initial(request)["task"]["requirements"] == ["all facts"]


def test_source_changes_invalidate_but_unchanged_build_files_do_not(context):
    for path in ("a.c", "b.c"):
        context.tools.execute("write_file", {"path": path, "content": "old"})
        read(context, path)
    context.tools.execute("replace_text", {"path": "a.c", "old": "old", "new": "new"})
    assert [o["read"]["path"] for o in initial(context.request({}))["current_observations"]] == ["b.c"]
    read(context, "a.c")
    # Model command effects are detected by hashes, without parsing command text.
    (context.tools.project / "a.c").write_text("command changed this")
    (context.tools.project / "build").mkdir()
    (context.tools.project / "build/a.o").write_bytes(b"binary")
    assert [o["read"]["path"] for o in initial(context.request({}))["current_observations"]] == ["b.c"]


def test_deleted_or_escaped_observation_is_not_reused(context):
    (context.tools.project / "a.c").write_text("old")
    read(context, "a.c")
    (context.tools.project / "a.c").unlink()
    assert initial(context.request({}))["current_observations"] == []
    (context.tools.project / "a.c").write_text("old")
    read(context, "a.c")
    (context.tools.project / "a.c").unlink()
    (context.tools.inputs / "protected").write_text("not source")
    (context.tools.project / "a.c").symlink_to(context.tools.inputs / "protected")
    assert initial(context.request({}))["current_observations"] == []


def test_latest_failure_diagnostic_survives_later_reads_and_eviction(context):
    diagnostic = {"tool_result": {"build": {"passed": False, "stderr": "actual compiler error"}},
                  "evidence_ref": {"path": "build-evidence", "sha256": "test"}}
    context.record('{"tool":"finish"}', diagnostic)
    for n in range(20):
        context.record(json.dumps({"tool": "search", "arguments": {"pattern": "x" * 5000}}), {"tool_result": {"matches": []}})
        request = context.request({"session": 1 + n // 7})
    assert context.evicted_transactions > 0
    assert initial(request)["latest_observed_diagnostic"] == diagnostic


def test_insufficient_capacity_does_not_silently_discard_source_or_last_result(context):
    (context.tools.project / "large.c").write_text("x" * 16000)
    read(context, "large.c")
    context.coder.context_max_bytes = 1000
    with pytest.raises(LLMRequestError, match="No current observation or latest diagnostic was silently dropped"):
        context.request({})
    assert len(context.observations) == 1
    assert len(context.transactions) == 1


def test_pointer_observations_are_versioned_by_original_file(context):
    path = context.tools.inputs / "spec.json"
    path.write_text('{"a": "fact"}')
    action = {"tool": "read_file", "arguments": {"path": "inputs/spec.json", "json_pointer": "/a"}}
    result = context.tools.execute("read_file", action["arguments"])
    context.record(json.dumps(action), {"tool_result": result, "evidence_ref": {}}, action=action)
    assert initial(context.request({}))["current_observations"][0]["result"]["content"] == '"fact"'
    Path(path).write_text('{"a": "changed"}')
    assert initial(context.request({}))["current_observations"] == []


def test_json_object_wire_bytes_are_included_in_context_limit(context):
    request = context.request({})
    context.coder.context_max_bytes = context.prepared_request.wire_bytes
    context.request({})
    from nepa.schemas import load_schema
    context.schema = load_schema("agent-action.schema.json")
    context.coder.action_format = "tool_calls"
    with pytest.raises(LLMRequestError):
        context.request({})


def test_selected_provider_prepares_actual_bytes_including_native_reasoning(context):
    from nepa.llm.client import LLMResponse, PreparedRequest
    from nepa.schemas import load_schema

    class DifferentWireProvider:
        def prepare(self, request, *, model, capabilities, native_schema=False):
            # Deliberately differs from the OpenAI payload in both shape and size.
            wire = {"selected_model": model, "native_request": request.model_dump(), "provider_overhead": "x" * 8000}
            body = json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
            return PreparedRequest("deepseek", model, wire, body, len(body), request.max_tokens + 10, capabilities)

    context.client.providers["deepseek"] = DifferentWireProvider()
    context.schema = load_schema("agent-action.schema.json")
    context.coder.action_format = "tool_calls"
    response = LLMResponse(text="", reasoning_content="思考" * 1000, tokens_in=1, tokens_out=1,
                           cost_cny=0, model=context.coder.model, parameter_support={},
                           tool_calls=[{"id": "native-id", "type": "function", "function": {
                               "name": "read_file", "arguments": '{"path":"a.c"}'}}])
    context.record(response, {"tool_result": {"error": "missing"}})
    context.request({})
    prepared = context.prepared_request
    assert prepared.wire["native_request"]["messages"][1] == response.assistant_message()
    assert prepared.wire["native_request"]["messages"][2]["tool_call_id"] == "native-id"
    assert prepared.billable_output_tokens == context.coder.max_tokens + 10
    context.coder.context_max_bytes = prepared.wire_bytes - 1
    with pytest.raises(LLMRequestError, match="context capacity exhausted"):
        context.request({})


def test_native_multiple_and_zero_calls_receive_whole_error_transactions(context):
    from nepa.llm.client import LLMResponse
    from nepa.schemas import load_schema
    context.coder.action_format = "tool_calls"
    context.schema = load_schema("agent-action.schema.json")
    response = LLMResponse(text="", reasoning_content="reasoning", tokens_in=1, tokens_out=1,
                           cost_cny=0, model=context.coder.model, parameter_support={},
                           tool_calls=[{"id": f"native-{i}", "type": "function", "function": {
                               "name": "write_file", "arguments": '{"path":"a.c","content":"x"}'}} for i in range(2)])
    context.record(response, {"format_errors": ["Exactly one tool required; no tool executed."]})
    request = context.request({})
    assert [m["role"] for m in request.messages] == ["user", "assistant", "tool", "tool"]
    assert [m["tool_call_id"] for m in request.messages[2:]] == ["native-0", "native-1"]
    response = response.model_copy(update={"tool_calls": []})
    context.record(response, {"format_errors": ["No tool executed."]})
    context.coder.context_max_bytes = context.prepared_request.wire_bytes
    request = context.request({})
    assert context.evicted_transactions == 1
    assert [m["role"] for m in request.messages] == ["user", "assistant", "user"]
