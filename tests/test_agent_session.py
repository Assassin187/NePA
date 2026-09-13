import json
from pathlib import Path
import pytest
from nepa.application import build_orchestrator
from nepa.config import load_config
from nepa.llm.client import LLMResponse
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
from nepa.run_store import RunStore

ROOT = Path(__file__).parents[1]

class SequenceProvider:
    native_structured_output = False
    def __init__(self, actions, config=None):
        self.actions = iter(actions)
        self.requests = []
        self.sent = []
        config = config or load_config()
        self.adapter = OpenAICompatibleProvider(config.coder.provider, config.providers[config.coder.provider])
    def prepare(self, request, **kwargs):
        self.current_request = request
        return self.adapter.prepare(request, **kwargs)
    def send(self, prepared):
        self.requests.append(self.current_request)
        self.sent.append(prepared)
        value = next(self.actions)
        if callable(value):
            value = value(self.current_request)
        if isinstance(value, LLMResponse):
            return value
        return LLMResponse(text=value if isinstance(value, str) else json.dumps(value), tokens_in=10, tokens_out=10, cost_cny=0,
                           model=prepared.model, parameter_support={}, provider_metadata={"finish_reason": "stop",
                           "returned_model_identity_observed": True, "returned_model_identity": prepared.model,
                           "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

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
        "deepseek-flash" if i < 3 + int(malformed is not None) else "deepseek-v4-pro" for i in range(calls)]
    if malformed is not None:
        assert "No tool executed" in provider.requests[1].messages[-1]["content"]
    assert "decisions_left" in provider.requests[-1].messages[-1]["content"]
    assert any("error:" in json.dumps(request.messages) for request in provider.requests[3:])
    assert any(message["role"] == "assistant" for message in provider.requests[-1].messages)
    assert (store.project / "build/san/protocol-server").is_file()
    assert all(request.system.count("Action schema:") == 1 for request in provider.requests)
    if long_history:
        observations = json.loads(provider.requests[-1].messages[0]["content"])["current_observations"]
        assert len(observations) == 1  # Repeated reads of unchanged code are deduplicated.
        for request, prepared in zip(provider.requests, provider.sent):
            assert prepared.wire_bytes <= 60000
            assert json.loads(request.messages[0]["content"].split("\nDecision budget:")[0])["task"] == store.plan()["tasks"][0]
    if malformed and malformed.startswith("{"):
        correction = provider.requests[1].messages[-1]["content"]
        assert "Invalid JSON" in correction and "closing braces" in correction


@pytest.mark.sandbox_integration
@pytest.mark.parametrize("single_model", [False, True])
def test_failed_compile_command_promotes_next_decision_within_same_budget(tmp_path, single_model):
    from test_execution_pipeline import MAKEFILE
    coder = {"model": "deepseek-v4-flash", "fast_model": None} if single_model else {
        "model": "deepseek-v4-pro", "fast_model": "deepseek-v4-flash"}
    config = load_config(overrides={"coder": coder, "budgets": {"sessions_per_task": 1, "decisions_per_session": 5}})
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", config)
    provider = SequenceProvider([
        action("write_file", path="Makefile", content=MAKEFILE),
        action("write_file", path="main.c", content="int main(void) { broken syntax }"),
        action("run_command", argv=["gcc", "-std=c99", "-c", "main.c"]),
        action("write_file", path="main.c", content="int main(void) { return 0; }"),
        action("finish", summary="fixed compiler failure", claims=[]),
    ], config)
    session = build_orchestrator(store, {"deepseek": provider}).session
    assert session.run(store.plan()["tasks"][0])
    assert [request.model for request in provider.requests] == ["deepseek-v4-flash"] * 3 + [config.coder.model] * 2
    progress = json.loads(provider.requests[3].messages[-1]["content"].split("\nDecision budget:")[1])
    assert progress["model_route"]["reason"] == "tool_failure_repair"
    assert progress["session"] == 1 and progress["decisions_left"] == 2
    result = json.loads(provider.requests[3].messages[-1]["content"].split("\nDecision budget:")[0])["tool_result"]
    assert result["returncode"] == 1 and "error:" in result["stderr"]
    assert store.run["tasks"]["bootstrap"]["sessions"] == 1
    assert store.run["tasks"]["bootstrap"]["decisions"] == store.run["budget"]["calls"] == 5
