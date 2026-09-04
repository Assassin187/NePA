import copy
import json
from pathlib import Path

import pytest

from nepa.config import load_config
from nepa.orchestrator import StageResult
from nepa.run_store import ArtifactConflict, RunStore, SpecRunInputs
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.stages.s4_planning import CandidateCompletion, complete_plan_candidate, publish_initial_plan


ROOT = Path(__file__).parents[1]


def _completion(tmp_path):
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
    shards = []
    for package in architecture["work_packages"]:
        shards.append({
            "schema_version": "1.0", "work_package_id": package["id"], "tasks": [{
                "local_id": "implement", "title": package["title"], "goal": package["goal"], "kind": "integration",
                "instructions": package["goal"], "deliverable_files": package["allowed_files"],
                "context_refs": package["context_refs"], "requirement_responsibilities": package["requirement_responsibilities"],
                "provides_contracts": package["provides_contracts"], "consumes_contracts": package["consumes_contracts"],
                "depends_on": [], "acceptance": {"build_variant_ids": ["san"], "tests": []},
            }],
        })
    run = store.load_run()
    frozen = {
        "spec_value": prepared.spec, "target_profile_value": prepared.target_profile, "test_bundle_value": prepared.test_bundle,
        "refs": {name: {"path": path, "sha256": run["inputs"][name]["sha256"]} for name, path in {
            "spec": "spec/spec.json", "target_profile": "inputs/target.json", "test_bundle": "inputs/test_bundle.json",
        }.items()},
    }
    draft = {"schema_version": "1.0", "architecture": architecture, "work_packages": architecture["work_packages"], "task_shards": shards}
    return store, complete_plan_candidate(draft, constraints, frozen, manifest, run["config_snapshot"])


def test_initial_publication_order_and_canonical_ledgers(tmp_path):
    store, completion = _completion(tmp_path)
    order = []
    result = publish_initial_plan(store, completion, fault_hook=order.append)

    assert isinstance(result, StageResult)
    assert order == ["plan_published", "file_ledger_published", "revision_ledger_published", "active_pointer_published", "semantic_reread"]
    plan = json.loads((store.root / "plan/versions/plan-1.0.0.json").read_text(encoding="utf-8"))
    active = json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8"))
    ledger = json.loads((store.root / "plan/file_ledger.json").read_text(encoding="utf-8"))
    revision = json.loads((store.root / "plan/revision_ledger.json").read_text(encoding="utf-8"))
    assert active == {"version": "1.0.0", "path": "plan/versions/plan-1.0.0.json", "sha256": result.output_refs["plan"]["sha256"], "revision_seq": 0, "epoch": "E0"}
    assert revision == {"schema_version": "1.0", "entries": []}
    assert [item["path"] for item in ledger["entries"]] == sorted(
        item["path"] for item in ledger["entries"]
    )
    expected = []
    for item in plan["architecture"]["layout"]["files"]:
        if item["path"] is not None:
            paths = [item["path"]]
        else:
            domain = sorted(set(completion.constraints["naming"]["message_ids" if item["expand_over"] == "messages" else "type_ids"].values()))
            placeholder = "{message_id}" if item["expand_over"] == "messages" else "{type_id}"
            paths = [item["path_pattern"].replace(placeholder, value) for value in domain]
        expected.extend(paths)
    assert {item["path"] for item in ledger["entries"]} == set(expected)
    serialized = json.dumps(plan)
    assert "task_uid" not in serialized
    assert "obligation_digest" not in serialized
    assert "guidance_digest" not in serialized


@pytest.mark.parametrize("point", ["plan_published", "file_ledger_published", "revision_ledger_published", "active_pointer_published", "semantic_reread"])
def test_publication_faults_leave_only_an_immutable_prefix(tmp_path, point):
    store, completion = _completion(tmp_path)

    def crash(current):
        if current == point:
            raise RuntimeError(current)

    with pytest.raises(RuntimeError, match=point):
        publish_initial_plan(store, completion, fault_hook=crash)
    if point == "plan_published":
        assert (store.root / "plan/versions/plan-1.0.0.json").is_file()
        assert not (store.root / "plan/file_ledger.json").exists()
    elif point == "file_ledger_published":
        assert (store.root / "plan/file_ledger.json").is_file()
        assert not (store.root / "plan/revision_ledger.json").exists()
    elif point == "revision_ledger_published":
        assert (store.root / "plan/revision_ledger.json").is_file()
        assert not (store.root / "plan/active_plan.json").exists()
    elif point == "active_pointer_published":
        assert (store.root / "plan/active_plan.json").is_file()
    else:
        assert (store.root / "plan/active_plan.json").is_file()


def test_publication_rejects_conflicting_existing_plan_bytes(tmp_path):
    store, completion = _completion(tmp_path)
    publish_initial_plan(store, completion)
    changed = copy.deepcopy(completion.plan)
    changed["review"]["unresolved_minor_issues"] = [{"id": "minor"}]
    conflicting = CandidateCompletion(
        plan_draft_ir=completion.plan_draft_ir, plan=changed, blueprint=completion.blueprint,
        link_report=completion.link_report, lint_report=completion.lint_report, constraints=completion.constraints,
        manifest=completion.manifest, spec=completion.spec, config_snapshot=completion.config_snapshot,
        input_refs=completion.input_refs,
    )
    with pytest.raises(ArtifactConflict):
        publish_initial_plan(store, conflicting)
