"""Run the temporary E2.1 staged ArchitecturePlanner experiment.

This file belongs to the disposable experiment directory.  It imports the
production binding and validator without modifying either of them.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import traceback

from nepa.agents.base import AgentInvoker
from nepa.calibration.s4_architecture import ArchitecturePlannerContractBinding, _derived_config
from nepa.config import load_config
from nepa.llm.client import LLMClient, StructuredOutputError
from nepa.llm.providers import OpenAICompatibleProvider
from nepa.llm.telemetry import LLMTelemetry
from nepa.run_store import RunStore
from nepa.speclib.architecture import validate_architecture
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.planning import (
    build_planning_index,
    build_test_manifest_metadata,
    prepare_architecture_inputs,
)


WORKSPACE = Path(__file__).resolve().parents[3]
EXPERIMENT = WORKSPACE / "experiments/m1-4a2-architecture-planner-prompt-optimization"
RUN_NAME = os.environ.get("NEPA_EXPERIMENT_RUN_NAME", "e2-1-s-staged")
ROOT = EXPERIMENT / "results/phase2/runs" / RUN_NAME
PROMPT_PATH = EXPERIMENT / "results/phase1/artifacts/prompt-exact-algorithm.md"
EXAMPLE_PATH = WORKSPACE / "nepa/schemas/examples/architecture-draft.example.json"
TRIAL_COUNT = 3


def checkpoint_issues(validation: dict, maximum_gate: int) -> list[dict]:
    return [
        issue
        for issue in validation["issues"]
        if int(issue["gate"].split("_")[1]) <= maximum_gate
    ]


def checkpoint_pass(validation: dict, maximum_gate: int) -> bool:
    return all(
        gate["verdict"] == "pass"
        for gate in validation["gates"]
        if int(gate["id"].split("_")[1]) <= maximum_gate
    )


def run_model(model_id: str, planning: dict, constraints: dict, manifest: dict, prompt: bytes, example: dict):
    config = load_config(WORKSPACE / "configs/m1-4a2-live.yaml")
    target = config.calibration_models[model_id]
    derived = _derived_config(config, target)
    model_root = ROOT / model_id
    model_root.mkdir(parents=True, exist_ok=True)
    store = RunStore(model_root)
    client = LLMClient(
        derived,
        {target.provider: OpenAICompatibleProvider(target.provider, derived.providers[target.provider])},
        store=store,
        telemetry=LLMTelemetry(
            store,
            secret_env_names={
                provider.api_key_env
                for provider in derived.providers.values()
                if provider.api_key_env
            },
        ),
    )
    binding = ArchitecturePlannerContractBinding(AgentInvoker(derived, client))
    binding.example = example
    trials = []

    for trial_number in range(1, TRIAL_COUNT + 1):
        trial_id = f"trial_{trial_number:03d}"
        previous = None
        previous_validation = None
        stages = []

        for stage_number, maximum_gate in ((1, 5), (2, 9), (3, 10)):
            if stage_number == 1:
                repair_context = None
            else:
                assert previous is not None and previous_validation is not None
                repair_context = {
                    "experiment_stage": stage_number,
                    "stage_goal": (
                        "Close modules/contracts/work-package files/exact contract-derived DAG; "
                        "do not change already passing arch_01 through arch_05."
                        if stage_number == 2
                        else
                        "Close requirement ownership and test readiness; do not change already "
                        "passing arch_01 through arch_09."
                    ),
                    "previous_candidate": previous,
                    "validation_issues": checkpoint_issues(previous_validation, maximum_gate),
                }
            try:
                result = binding.invoke(
                    planning_index=planning,
                    delivery_constraints=constraints,
                    repair_context=repair_context,
                    run_id=f"experiment:{RUN_NAME}:{model_id}",
                    task_id=trial_id,
                    attempt=stage_number,
                    use_cache=False,
                    template_bytes=prompt,
                )
                candidate = result.parsed
                validation = validate_architecture(candidate, planning, manifest, constraints)
                candidate_name = f"{trial_id}_stage{stage_number}.candidate.json"
                validation_name = f"{trial_id}_stage{stage_number}.validation.json"
                (model_root / candidate_name).write_bytes(canonical_json_bytes(candidate))
                (model_root / validation_name).write_bytes(canonical_json_bytes(validation))
                response = result.response
                stages.append(
                    {
                        "stage": stage_number,
                        "checkpoint_max_gate": maximum_gate,
                        "checkpoint_pass": checkpoint_pass(validation, maximum_gate),
                        "full_verdict": validation["verdict"],
                        "failed_gates": [
                            gate["id"] for gate in validation["gates"] if gate["verdict"] == "fail"
                        ],
                        "issue_codes": dict(Counter(issue["code"] for issue in validation["issues"])),
                        "candidate_path": candidate_name,
                        "validation_path": validation_name,
                        "usage": {
                            "tokens_in": response.tokens_in,
                            "tokens_out": response.tokens_out,
                            "cost_usd": response.cost_usd,
                            "model": response.model,
                            "format_repair_attempts": response.repair_attempts,
                            "finish_reason": response.provider_metadata.get("finish_reason"),
                        },
                    }
                )
                previous = candidate
                previous_validation = validation
            except StructuredOutputError as error:
                stages.append(
                    {
                        "stage": stage_number,
                        "checkpoint_max_gate": maximum_gate,
                        "checkpoint_pass": False,
                        "full_verdict": "not-evaluable",
                        "schema_errors": error.errors,
                        "error": str(error),
                    }
                )
                break
            except Exception as error:  # retain infrastructure evidence
                stages.append(
                    {
                        "stage": stage_number,
                        "checkpoint_max_gate": maximum_gate,
                        "checkpoint_pass": False,
                        "full_verdict": "infrastructure-invalid",
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "traceback": traceback.format_exc(),
                    }
                )
                break

        record = {"trial_id": trial_id, "stages": stages}
        trials.append(record)
        (model_root / f"{trial_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        )
    return model_id, trials


def summarize(model_trials: list[dict]) -> dict:
    checkpoint_passed = {"stage1": 0, "stage2": 0, "stage3": 0}
    final_passed = 0
    infrastructure_invalid = 0
    regression_count = 0
    final_issue_codes = Counter()
    total_calls = 0
    tokens_in = 0
    tokens_out = 0

    for trial in model_trials:
        stages = trial["stages"]
        infrastructure_invalid += any(
            stage["full_verdict"] == "infrastructure-invalid" for stage in stages
        )
        for index, stage in enumerate(stages, start=1):
            checkpoint_passed[f"stage{index}"] += bool(stage["checkpoint_pass"])
            if "usage" in stage:
                total_calls += 1 + int(stage["usage"]["format_repair_attempts"])
                tokens_in += stage["usage"]["tokens_in"]
                tokens_out += stage["usage"]["tokens_out"]
        evaluable = [stage for stage in stages if stage["full_verdict"] in {"pass", "fail"}]
        if evaluable:
            final = evaluable[-1]
            final_passed += final["full_verdict"] == "pass"
            final_issue_codes.update(final["issue_codes"])
            for before, after in zip(evaluable, evaluable[1:]):
                before_pass = {f"arch_{gate:02d}" for gate in range(1, 11)} - set(before["failed_gates"])
                regression_count += len(before_pass & set(after["failed_gates"]))

    return {
        "trial_count": len(model_trials),
        "checkpoint_passed": checkpoint_passed,
        "final_passed": final_passed,
        "infrastructure_invalid_trials": infrastructure_invalid,
        "repair_regressions": regression_count,
        "final_issue_codes": dict(final_issue_codes),
        "provider_call_count": total_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "trials": model_trials,
    }


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT_PATH.read_bytes()
    example = json.loads(EXAMPLE_PATH.read_text())
    prepared = prepare_architecture_inputs(
        WORKSPACE / "gold_file/specIR.json",
        WORKSPACE / "gold_file/target.json",
        WORKSPACE / "gold_file/test_bundle.json",
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    metadata = {
        "experiment_id": RUN_NAME,
        "trial_count": TRIAL_COUNT,
        "models": ["qwen", "deepseek"],
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "example_sha256": hashlib.sha256(canonical_json_bytes(example)).hexdigest(),
        "call_shape": "three full-schema stages with checkpoints 01-05, 01-09, 01-10",
        "schema_presentation": "current duplicate non-native fallback",
        "input_ledger": False,
    }
    (ROOT / "arm-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(
            pool.map(
                lambda model_id: run_model(
                    model_id, planning, constraints, manifest, prompt, example
                ),
                ("qwen", "deepseek"),
            )
        )
    summary = {"arm": metadata, "models": {key: summarize(value) for key, value in results.items()}}
    (ROOT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                model_id: {
                    "checkpoint_passed": value["checkpoint_passed"],
                    "final_passed": value["final_passed"],
                    "infrastructure_invalid_trials": value["infrastructure_invalid_trials"],
                }
                for model_id, value in summary["models"].items()
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
