"""Generate the frozen MQTT and non-MQTT S5 inputs through the S4 path."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from nepa.config import load_config
from nepa.run_store import RunStore, SpecRunInputs
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
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
