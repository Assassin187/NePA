"""Generate the frozen MQTT and non-MQTT S5 inputs through the S4 path."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path
from typing import Any

from nepa.config import load_config
from nepa.run_store import RunStore, SpecRunInputs, sha256_bytes
from nepa.speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.materialization import build_artifact_manifest, build_contract_map, derive_rendering_view, render_e0_files
from nepa.speclib.plan_revision import build_revision_entry, classify_migration
from nepa.speclib.plan import blueprint_task_semantic_projection, derive_task_metadata
from nepa.speclib.plan_state import initialize_plan_state
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.stages.s4_planning import complete_plan_candidate, publish_initial_plan


ROOT = Path(__file__).resolve().parents[2]


def _shards(architecture: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for package in architecture["work_packages"]:
        result.append(
            {
                "schema_version": "1.0",
                "work_package_id": package["id"],
                "tasks": [
                    {
                        "local_id": "implement",
                        "title": package["title"],
                        "goal": package["goal"],
                        "kind": "integration",
                        "instructions": package["goal"],
                        "deliverable_files": package["allowed_files"],
                        "context_refs": package["context_refs"],
                        "requirement_responsibilities": package["requirement_responsibilities"],
                        "provides_contracts": package["provides_contracts"],
                        "consumes_contracts": package["consumes_contracts"],
                        "depends_on": [],
                        "acceptance": {"build_variant_ids": ["san"], "tests": []},
                    }
                ],
            }
        )
    return result


def _canonical(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _structural_plan(plan: dict[str, Any], constraints: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = copy.deepcopy(plan)
    old_path = "src/codec/codec.c"
    new_path = "src/codec/codec_v2.c"
    for row in candidate["architecture"]["layout"]["files"]:
        if row.get("path") == old_path:
            row["path"] = new_path
    for package in candidate["work_packages"]:
        package["allowed_files"] = [new_path if path == old_path else path for path in package["allowed_files"]]
    contracts = {row["id"]: row for row in candidate["architecture"]["contracts"]}
    for task in candidate["tasks"]:
        task["deliverable_files"] = [new_path if path == old_path else path for path in task["deliverable_files"]]
        task.update(derive_task_metadata(task, contracts=contracts))
    blueprint = compile_delivery_blueprint(
        constraints, candidate["architecture"], candidate["work_packages"],
        blueprint_task_semantic_projection(candidate["tasks"]),
    )
    candidate["delivery_blueprint_sha256"] = sha256_bytes(_canonical(blueprint))
    return candidate, blueprint


def _epoch_inputs(completion: Any, publication: Any, store: RunStore, case_id: str) -> dict[str, Any]:
    """Build deterministic, artificial F2/F3 inputs from the accepted E0 shape."""

    plan = completion.plan
    active = json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8"))
    file_ledger = json.loads((store.root / "plan/file_ledger.json").read_text(encoding="utf-8"))
    state = initialize_plan_state(plan, plan_ref=active)
    view = derive_rendering_view(plan, completion.spec, completion.constraints["target_profile"], completion.blueprint, completion.constraints)
    rendered = render_e0_files(view, completion.spec, completion.constraints["target_profile"], completion.blueprint, completion.constraints)
    plan_sha = store._canonical_value_hash(plan)
    f2 = {"version": "1.0.1", "path": "plan/versions/plan-1.0.1.json", "sha256": plan_sha, "revision_seq": 1, "epoch": "E0"}
    f3_plan, f3_blueprint = _structural_plan(plan, completion.constraints)
    f3_sha = store._canonical_value_hash(f3_plan)
    f3 = {"version": "1.1.0", "path": "plan/versions/plan-1.1.0.json", "sha256": f3_sha, "revision_seq": 2, "epoch": "E1"}
    f2_migration = classify_migration(plan, plan, state, file_ledger, from_version="1.0.0", to_version="1.0.1")
    f3_migration = classify_migration(plan, f3_plan, state, file_ledger, from_version="1.0.1", to_version="1.1.0")
    owner = next(task for task in f3_plan["tasks"] if "src/codec/codec_v2.c" in task["deliverable_files"])
    f3_migration["pending_groups"] = [{
        "group_id": "g-2-1", "member_task_uids": [owner["task_uid"]],
        "affected_paths": ["src/codec/codec_v2.c"], "affected_symbols": [],
        "build_artifact_ids": [f3_blueprint["build_artifacts"][0]["id"]],
    }]
    gates = {f"RG-{index}": "pass" for index in range(1, 6)}
    f2_entry = build_revision_entry(active, f2, "F2", {"code": "fixture", "evidence_refs": []}, [], f2_migration, gates=gates, activated_at_commit="0" * 40)
    f3_entry = build_revision_entry(f2, f3, "F3", {"code": "fixture", "evidence_refs": []}, [], f3_migration, gates=gates, activated_at_commit="0" * 40)
    plan_output = dict(publication.output_refs["plan"])
    active_output = dict(publication.output_refs["active_plan"])
    return {
        "schema_version": "1.0",
        "fixture_id": f"{case_id}-multi-epoch",
        "protocol": case_id,
        "source_e0": {
            "plan_ref": plan_output,
            "active_plan": active_output,
            "file_ledger": {"path": "file_ledger.json", "sha256": store._canonical_value_hash(file_ledger)},
            "state": state,
            "workspace_file_hashes": {path: sha256_bytes(data) for path, data in sorted(rendered.items())},
            "manifest": build_artifact_manifest(plan_output, completion.blueprint, {**view, "rendered_files": rendered}, "E0"),
            "contract_map": build_contract_map(plan_output, completion.blueprint, {**view, "rendered_files": rendered}, "E0"),
        },
        "f2": {"plan_ref": f2, "plan": plan, "activation_input": f2_entry, "migration": f2_migration},
        "f3": {"plan_ref": f3, "plan": f3_plan, "blueprint": f3_blueprint, "activation_input": f3_entry, "migration": f3_migration, "predecessor": {"epoch": "E0", "checkpoint": None}},
    }


def _build_case(case_id: str, source: Path, output: Path) -> None:
    inputs = SpecRunInputs(source / "specIR.json" if case_id == "mqtt" else source / "spec.json", source / "target.json", source / "test_bundle.json")
    with tempfile.TemporaryDirectory(prefix=f"nepa-s5-{case_id}-") as directory:
        store = RunStore.initialize_spec_run(Path(directory), inputs, load_config())
        prepared = prepare_architecture_inputs(store.root / "spec/spec.json", store.root / "inputs/target.json", store.root / "inputs/test_bundle.json")
        constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
        manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
        architecture = json.loads((source / "architecture-draft.json").read_text(encoding="utf-8"))
        frozen = {
            "spec_value": prepared.spec,
            "target_profile_value": prepared.target_profile,
            "test_bundle_value": prepared.test_bundle,
            "refs": {
                name: {"path": path, "sha256": store.load_run()["inputs"][name]["sha256"]}
                for name, path in {
                    "spec": "spec/spec.json",
                    "target_profile": "inputs/target.json",
                    "test_bundle": "inputs/test_bundle.json",
                }.items()
            },
        }
        draft = {"schema_version": "1.0", "architecture": architecture, "work_packages": architecture["work_packages"], "task_shards": _shards(architecture)}
        completion = complete_plan_candidate(draft, constraints, frozen, manifest, store.load_run()["config_snapshot"])
        publication = publish_initial_plan(store, completion)
        store.publish_immutable_json("plan/_s4/delivery_constraints.json", constraints)

        output.mkdir(parents=True, exist_ok=True)
        sources = {
            "spec.json": "spec/spec.json",
            "target.json": "inputs/target.json",
            "test_bundle.json": "inputs/test_bundle.json",
            "plan.json": "plan/versions/plan-1.0.0.json",
            "blueprint.json": "plan/_s4/delivery_blueprint.json",
            "active_plan.json": "plan/active_plan.json",
            "file_ledger.json": "plan/file_ledger.json",
            "revision_ledger.json": "plan/revision_ledger.json",
            "constraints.json": "plan/_s4/delivery_constraints.json",
        }
        for destination, relative in sources.items():
            value = json.loads((store.root / relative).read_text(encoding="utf-8"))
            (output / destination).write_bytes(_canonical(value))
        (output / "epoch_inputs.json").write_bytes(_canonical(_epoch_inputs(completion, publication, store, case_id)))
        run = store.load_run()
        metadata = {
            "schema_version": "1.0",
            "fixture_id": case_id,
            "generated_via": "nepa.stages.s4_planning.complete_plan_candidate + publish_initial_plan",
            "source_directory": str(source.relative_to(ROOT)),
            "s4_anchors": {
                "plan": dict(publication.output_refs["plan"]),
                "active_plan": dict(publication.output_refs["active_plan"]),
                "delivery_blueprint_sha256": publication.output_refs["delivery_blueprint_sha256"],
            },
            "config_snapshot_sha256": run["config_snapshot_sha256"],
        }
        (output / "metadata.json").write_bytes(_canonical(metadata))


def generate(output: Path) -> None:
    _build_case("mqtt", ROOT / "gold_file", output / "mqtt")
    _build_case("non_mqtt", ROOT / "tests/fixtures/non_mqtt_application", output / "non_mqtt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.output.resolve())
