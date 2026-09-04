import copy
import json
from pathlib import Path

from nepa.config import load_config
from nepa.run_store import RunStore, SpecRunInputs
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs
from nepa.stages.s4_planning import complete_plan_candidate


ROOT = Path(__file__).parents[1]


def _shard(package):
    return {
        "schema_version": "1.0", "work_package_id": package["id"], "tasks": [{
            "local_id": "implement", "title": package["title"], "goal": package["goal"], "kind": "integration",
            "instructions": package["goal"], "deliverable_files": package["allowed_files"],
            "context_refs": package["context_refs"], "requirement_responsibilities": package["requirement_responsibilities"],
            "provides_contracts": package["provides_contracts"], "consumes_contracts": package["consumes_contracts"],
            "depends_on": [], "acceptance": {"build_variant_ids": ["san"], "tests": []},
        }],
    }


def test_layered_and_flat_semantic_drafts_share_one_byte_stable_completion(tmp_path):
    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(),
    )
    prepared = prepare_architecture_inputs(
        store.root / "spec/spec.json", store.root / "inputs/target.json", store.root / "inputs/test_bundle.json"
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    architecture = json.loads((ROOT / "gold_file/architecture-draft.json").read_text(encoding="utf-8"))
    packages = copy.deepcopy(architecture["work_packages"])
    shards = [_shard(package) for package in packages]
    frozen = {
        "spec_value": prepared.spec,
        "target_profile_value": prepared.target_profile,
        "test_bundle_value": prepared.test_bundle,
        "refs": {
            "spec": {"path": "spec/spec.json", "sha256": store.load_run()["inputs"]["spec"]["sha256"]},
            "target_profile": {"path": "inputs/target.json", "sha256": store.load_run()["inputs"]["target_profile"]["sha256"]},
            "test_bundle": {"path": "inputs/test_bundle.json", "sha256": store.load_run()["inputs"]["test_bundle"]["sha256"]},
        },
    }
    config = store.load_run()["config_snapshot"]
    layered = {"schema_version": "1.0", "architecture": architecture, "work_packages": packages, "task_shards": shards}
    flat = {"schema_version": "1.0", "architecture": architecture, "work_packages": list(reversed(packages)), "task_shards": list(reversed(shards))}

    left = complete_plan_candidate(layered, constraints, frozen, manifest, config)
    right = complete_plan_candidate(flat, constraints, frozen, manifest, config)

    assert left.plan_draft_ir == right.plan_draft_ir
    assert left.plan == right.plan
    assert left.blueprint == right.blueprint
    assert left.link_report == right.link_report
    assert left.lint_report == right.lint_report
