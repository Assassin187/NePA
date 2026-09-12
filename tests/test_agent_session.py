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
        return LLMResponse(text=json.dumps(value), tokens_in=10, tokens_out=10, cost_usd=0,
                           model=model, parameter_support={}, provider_metadata={"finish_reason": "stop"})

def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}

@pytest.mark.sandbox_integration
def test_agent_uses_actual_diagnostic_and_fixes_code(tmp_path):
    config = load_config(overrides={"budgets": {"sessions_per_task": 1}})
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/specIR.json",
                                ROOT / "gold_file/target.json", ROOT / "gold_file/acceptance.json", config)
    makefile = ("release:\n\tmkdir -p build/release\n\tgcc -std=c99 -Wall -Wextra -Werror main.c -o build/release/protocol-server\n"
                "san:\n\tmkdir -p build/san\n\tgcc -std=c99 -Wall -Wextra -Werror -fsanitize=address,undefined main.c -o build/san/protocol-server\n"
                "clean:\n\trm -rf build\n")
    provider = SequenceProvider([
        action("write_file", path="Makefile", content=makefile),
        action("write_file", path="main.c", content="int main(void){ broken syntax }"),
        action("finish", summary="request checks", claims=[]),
        action("read_file", path="main.c"),
        action("write_file", path="main.c", content="int main(void){return 0;}"),
        action("finish", summary="repaired compiler error", claims=[]),
    ])
    session = build_orchestrator(store, {"deepseek": provider}).session
    assert session.run(store.plan()["tasks"][0])
    assert store.run["tasks"]["bootstrap"]["status"] == "passed"
    assert store.run["budget"]["calls"] == 6
    assert any("error:" in request.user for request in provider.requests[3:])
    assert (store.project / "build/san/protocol-server").is_file()
    assert all(request.system.count("Action schema:") == 1 for request in provider.requests)
