import json
from pathlib import Path
import pytest
from nepa.application import build_orchestrator
from nepa.config import load_config
from nepa.llm.client import LLMResponse
from nepa.run_store import RunStore

ROOT = Path(__file__).parents[1]

class SequenceProvider:
    native_structured_output = False
    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []
    def complete(self, request, *, model, native_schema):
        self.requests.append(request)
        value = next(self.actions)
        return LLMResponse(text=value if isinstance(value, str) else json.dumps(value), tokens_in=10, tokens_out=10, cost_cny=0,
                           model=model, parameter_support={}, provider_metadata={"finish_reason": "stop"})

def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}

@pytest.mark.sandbox_integration
@pytest.mark.parametrize("long_history", [False, True])
@pytest.mark.parametrize("malformed", [None, "", "<tool_calls><invoke name='write_file'/></tool_calls>",
                                       '{"tool":"finish","arguments":{"summary":"ready","claims":[]}'])
def test_agent_uses_actual_diagnostic_and_fixes_code(tmp_path, malformed, long_history):
    config = load_config(overrides={"budgets": {"sessions_per_task": 3, "decisions_per_session": 5},
                                    "coder": {"context_max_bytes": 60000, "fast_model": "deepseek-flash", "action_format": "json_object"}})
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", config)
    makefile = ("release:\n\tmkdir -p build/release\n\tgcc -std=c99 -Wall -Wextra -Werror main.c -o build/release/protocol-server\n"
                "san:\n\tmkdir -p build/san\n\tgcc -std=c99 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-pie -no-pie main.c -o build/san/protocol-server\n"
                "clean:\n\trm -rf build\n")
    provider = SequenceProvider(([malformed] if malformed is not None else []) + [
        action("write_file", path="Makefile", content=makefile),
        action("write_file", path="main.c", content="int main(void){ broken syntax }"),
        action("finish", summary="request checks", claims=[]),
        action("read_file", path="main.c"),
        action("write_file", path="main.c", content="int main(void){return 0;}" + ("/*" + "x" * 10000 + "*/" if long_history else "")),
    ] + ([action("read_file", path="main.c")] * 7 if long_history else []) + [
        action("finish", summary="repaired compiler error", claims=[]),
    ])
    session = build_orchestrator(store, {"deepseek": provider}).session
    assert session.run(store.plan()["tasks"][0])
    assert store.run["tasks"]["bootstrap"]["status"] == "passed"
    calls = 6 + int(malformed is not None) + (7 if long_history else 0)
    assert store.run["budget"]["calls"] == calls
    assert store.run["tasks"]["bootstrap"]["sessions"] == (calls + 4) // 5
    for request in provider.requests:
        assert request.action_format == "json_object"
        assert [m["role"] for m in request.messages] == ["user"] + ["assistant", "user"] * ((len(request.messages) - 1) // 2)
    assert [request.model for request in provider.requests] == [
        "deepseek-flash" if i < 5 else "deepseek-v4-pro" for i in range(calls)]
    if malformed is not None:
        assert "No tool executed" in provider.requests[1].messages[-1]["content"]
    assert "decisions_left" in provider.requests[-1].messages[-1]["content"]
    assert any("error:" in json.dumps(request.messages) for request in provider.requests[3:])
    assert any(message["role"] == "assistant" for message in provider.requests[-1].messages)
    assert (store.project / "build/san/protocol-server").is_file()
    assert all(request.system.count("Action schema:") == 1 for request in provider.requests)
    final_observations = json.loads(provider.requests[-1].messages[0]["content"])["current_observations"]
    assert any("return 0" in observation["result"]["content"] for observation in final_observations)
    assert any(observation["evidence_ref"]["path"].startswith("evidence/actions/")
               for observation in final_observations)
    edit_receipts = []
    for message in provider.requests[-1].messages:
        if message["role"] != "user":
            continue
        try:
            receipt = json.loads(message["content"])
        except json.JSONDecodeError:
            continue
        if "observation_refresh" in receipt.get("tool_result", {}):
            edit_receipts.append(receipt["tool_result"]["observation_refresh"])
    assert edit_receipts and all("observations" not in receipt for receipt in edit_receipts)
    request_evidence = json.loads((store.root / "evidence/calls/000001.request.json").read_text())
    assert request_evidence["context"]["session"] == 1
    assert request_evidence["context"]["decision"] == 1
    performance = list((store.root / "evidence/performance").rglob("*.json"))
    assert any(json.loads(path.read_text())["event"] == "task_invocation_finished" for path in performance)
    if long_history:
        from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
        observations = json.loads(provider.requests[-1].messages[0]["content"])["current_observations"]
        assert len(observations) == 1  # Repeated reads of unchanged code are deduplicated.
        for request in provider.requests:
            wire = OpenAICompatibleProvider._payload(request, config.coder.model, False)
            assert len(json.dumps(wire, ensure_ascii=False).encode()) <= 60000
            assert json.loads(request.messages[0]["content"].split("\nDecision budget:")[0])["task"] == store.plan()["tasks"][0]
    if malformed and malformed.startswith("{"):
        correction = provider.requests[1].messages[-1]["content"]
        assert "Invalid JSON" in correction and "required delimiters" in correction


def test_format_feedback_is_classified_persisted_and_executes_nothing(tmp_path):
    config = load_config(overrides={"budgets": {"sessions_per_task": 1, "decisions_per_session": 1}})
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", config)
    provider = SequenceProvider(['{"tool":"list_files","arguments":{}}<invoke/>'])
    session = build_orchestrator(store, {"deepseek": provider}).session
    assert not session.run(store.plan()["tasks"][0])
    feedback = store.run["tasks"]["bootstrap"]["last_feedback"]
    assert feedback["format_errors"][0]["code"] == "trailing_data"
    assert "no prose, tags" in feedback["instruction"]
    assert feedback["response_ref"]["path"].endswith("000001.response.json")
    assert "finish_format" not in feedback
    assert not list((store.root / "evidence/actions").glob("*.json"))
