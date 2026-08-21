"""Build deterministic quality indicators for inspected final candidates."""

from __future__ import annotations

import json
from pathlib import Path

from nepa.speclib.architecture import validate_architecture
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import (
    build_planning_index,
    build_test_manifest_metadata,
    prepare_architecture_inputs,
)


WORKSPACE = Path(__file__).resolve().parents[3]
EXPERIMENT = WORKSPACE / "experiments/m1-4a2-architecture-planner-prompt-optimization"


def final_candidate(directory: Path, trial_id: str) -> Path:
    matches = sorted(directory.glob(f"{trial_id}_p*.candidate.json"))
    if not matches:
        matches = sorted(directory.glob(f"candidates/{trial_id}_p*.json"))
    if not matches:
        raise FileNotFoundError(f"no candidate for {directory}/{trial_id}")
    return matches[-1]


def indicators(label: str, path: Path, planning: dict, manifest: dict, constraints: dict) -> dict:
    candidate = json.loads(path.read_text())
    validation = validate_architecture(candidate, planning, manifest, constraints)
    work_packages = candidate["work_packages"]
    primary_counts = [
        sum(item["role"] == "primary" for item in work_package["requirement_responsibilities"])
        for work_package in work_packages
    ]
    supporting_counts = [
        sum(item["role"] == "supporting" for item in work_package["requirement_responsibilities"])
        for work_package in work_packages
    ]
    task_contracts = [contract for contract in candidate["contracts"] if contract["ready_gate"] == "task"]
    primary_total = sum(primary_counts)
    return {
        "label": label,
        "path": str(path.relative_to(WORKSPACE)),
        "validator_verdict": validation["verdict"],
        "module_count": len(candidate["modules"]),
        "work_package_count": len(work_packages),
        "contract_count": len(candidate["contracts"]),
        "task_contract_count": len(task_contracts),
        "primary_total": primary_total,
        "supporting_total": sum(supporting_counts),
        "primary_counts_by_wp": dict(zip((wp["id"] for wp in work_packages), primary_counts)),
        "supporting_counts_by_wp": dict(zip((wp["id"] for wp in work_packages), supporting_counts)),
        "maximum_primary_share": max(primary_counts, default=0) / primary_total if primary_total else 0,
        "zero_primary_work_packages": [
            work_packages[index]["id"] for index, count in enumerate(primary_counts) if count == 0
        ],
        "zero_responsibility_work_packages": [
            work_packages[index]["id"]
            for index, (primary, supporting) in enumerate(zip(primary_counts, supporting_counts))
            if primary + supporting == 0
        ],
        "task_contracts_without_consumers": [
            contract["id"] for contract in task_contracts if not contract["consumers"]
        ],
        "task_contract_interface_file_count": {
            contract["id"]: len(contract["interface_files"]) for contract in task_contracts
        },
        "empty_context_ref_decisions": sum(not item["context_refs"] for item in candidate["decisions"]),
        "decision_count": len(candidate["decisions"]),
    }


def main() -> None:
    prepared = prepare_architecture_inputs(
        WORKSPACE / "gold_file/specIR.json",
        WORKSPACE / "gold_file/target.json",
        WORKSPACE / "gold_file/test_bundle.json",
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)

    paths: list[tuple[str, Path]] = [
        (
            "baseline-v0-deepseek-trial002-p1",
            EXPERIMENT / "results/phase0/blind/candidates/candidate-c.json",
        )
    ]
    phase1 = EXPERIMENT / "results/phase1/runs/e1-1-b-exact-prompt"
    extension = (
        EXPERIMENT
        / "results/phase1/runs/e1-1-b-n5-extension/_calibration/s4-architecture"
        / "daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772"
        / "e1-1-b-exact-prompt"
    )
    for model_id in ("qwen", "deepseek"):
        for trial_number in range(1, 4):
            trial_id = f"trial_{trial_number:03d}"
            paths.append(
                (
                    f"exact-{model_id}-{trial_id}",
                    final_candidate(phase1 / model_id, trial_id),
                )
            )
        for trial_number in range(4, 6):
            trial_id = f"trial_{trial_number:03d}"
            paths.append(
                (
                    f"exact-{model_id}-{trial_id}",
                    final_candidate(extension / model_id, trial_id),
                )
            )

    values = [indicators(label, path, planning, manifest, constraints) for label, path in paths]
    output = EXPERIMENT / "results/phase1/quality-inspection.json"
    output.write_text(json.dumps({"candidates": values}, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
