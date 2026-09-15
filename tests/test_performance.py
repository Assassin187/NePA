import json
from pathlib import Path

from nepa.config import load_config
from nepa.llm.client import LLMResponse
from nepa.performance import summarize_run
from nepa.run_store import RunStore


ROOT = Path(__file__).parents[1]


def test_performance_summary_uses_strict_decoder_and_durable_evidence(tmp_path):
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                ROOT / "gold_file/mqtt/target.json",
                                ROOT / "gold_file/mqtt/acceptance.json", load_config())
    context = {"session": 1, "decision": 1, "route": {"reason": "fixture"},
               "context_assembly_elapsed_s": .01}
    sequence = store.reserve_call("bootstrap", .01, {"messages": []}, context=context)
    response = LLMResponse(
        text='{"tool":"list_files","arguments":{}} trailing', tokens_in=10, tokens_out=5,
        cost_cny=.001, model="deepseek-flash", parameter_support={},
        pricing={"cache_hit_tokens": 8, "cache_miss_tokens": 2},
        provider_metadata={"finish_reason": "stop"},
    )
    store.settle_call(sequence, response.model_dump(mode="json"), elapsed_s=2.5)
    identifier = store.start_action("bootstrap", {"tool": "run_command", "arguments": {"argv": ["false"]}},
                                    context=context)
    store.finish_action(identifier, {"returncode": 1, "duration_ms": 125, "timed_out": False},
                        execution_elapsed_s=.2)

    summary = summarize_run(store.root)
    assert summary["llm"]["provider_attempts"] == summary["llm"]["completed_responses"] == 1
    assert summary["llm"]["logical_decisions_observed"] == 1
    assert summary["llm"]["rejected_actions"] == 1
    assert summary["llm"]["error_categories"] == {"trailing_data": 1}
    assert summary["llm"]["cache_hit_rate"] == .8
    assert summary["actions"]["failed_total"] == 1
    assert summary["actions"]["measured_execution_elapsed_s"] == .2
    assert summary["actions"]["run_command_elapsed_s"] == .125
    assert summary["accounting"]["budget"]["calls"] == 1
    assert summary["accounting"]["unknown_reserved_cny"] == 0
    assert json.loads(json.dumps(summary))["run_id"] == store.run_id
