import copy
from pathlib import Path

from jsonschema import Draft202012Validator

from nepa.config import load_config
from nepa.orchestrator import StageContext
from nepa.run_store import RunStore, SpecRunInputs
from nepa.schemas import s4_commitment_contract
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.stages.s4_planning import S4Controller, build_s4_commitment


ROOT = Path(__file__).parents[1]


def _store(tmp_path):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(),
    )


def test_s4_commitment_is_canonical_and_contains_closed_projections(tmp_path):
    store = _store(tmp_path)
    prepared = prepare_architecture_inputs(
        store.root / "spec/spec.json", store.root / "inputs/target.json", store.root / "inputs/test_bundle.json"
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    first = build_s4_commitment(store.load_run(), prepared, constraints, manifest)
    second = build_s4_commitment(copy.deepcopy(store.load_run()), prepared, constraints, manifest)
    schema, _example = s4_commitment_contract()

    assert first == second
    assert Draft202012Validator(schema).is_valid(first)
    assert first["strategy"] == "layered"
    assert set(first["requirements"][0]) == {"id", "level"}
    assert set(first["test_manifest"]["tests"][0]) == {
        "nodeid", "layer", "description", "req_ids", "gate", "build_variant_ids",
    }
    assert set(first["layer_switches"]) == {"l0", "l1", "l2", "l3"}


def test_prepare_publishes_commitment_and_planning_evidence_without_provider_io(tmp_path):
    store = _store(tmp_path)
    controller = S4Controller(object())
    prepared, constraints, manifest, planning_index, commitment_ref, _book = controller._prepare(
        StageContext(store, "s4", store.load_run(), object())
    )

    assert prepared.spec["protocol"]
    assert constraints["target_support"]["supported"] is True
    assert manifest["tests"]
    assert planning_index["requirements"]
    assert commitment_ref["path"].startswith("plan/_s4/checkpoints/0001-commitment-commitment.json")
    for path in ("plan/_s4/commitment.json", "plan/_s4/planning_index.json", "plan/_s4/delivery_constraints.json", "plan/_s4/test_manifest_metadata.json"):
        assert (store.root / path).is_file()
