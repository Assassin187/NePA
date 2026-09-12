import json
from pathlib import Path

import pytest

from nepa.agents.context import CodingContext
from nepa.config import CoderConfig
from nepa.llm.client import LLMRequestError
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.workspace import WorkspaceTools


@pytest.fixture
def context(tmp_path):
    for name in ("project", "inputs", "evidence"):
        (tmp_path / name).mkdir()
    tools = WorkspaceTools(tmp_path / "project", tmp_path / "inputs", tmp_path / "evidence",
                           SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    return CodingContext("one action", {"task": {"id": "generic", "requirements": ["all facts"]}},
                         CoderConfig(context_max_bytes=50000), tools)


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
            payload = OpenAICompatibleProvider._payload(request, context.coder.model, False)
            assert len(json.dumps(payload, ensure_ascii=False).encode()) <= 50000
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
