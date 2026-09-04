import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).parents[1]
SCHEMA_DIR = ROOT / "nepa" / "schemas"
EXAMPLE_DIR = SCHEMA_DIR / "examples"
NEW_CONTRACTS = (
    "s4-commitment",
    "s4-checkpoint",
    "s4-state",
    "plan-critic-result",
    "flat-plan-draft",
    "active-plan",
    "file-ledger",
    "revision-ledger",
)


def _contract(name):
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    example = json.loads((EXAMPLE_DIR / f"{name}.example.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema, example


def test_s4_contract_examples_are_mutually_validated():
    for name in NEW_CONTRACTS:
        schema, example = _contract(name)
        Draft202012Validator(schema).validate(example)


def test_initial_contracts_reject_later_revision_or_runtime_fields():
    for name, field in (
        ("active-plan", "revision_entry"),
        ("file-ledger", "owner_task_id"),
        ("revision-ledger", "migration"),
        ("flat-plan-draft", "coverage"),
    ):
        schema, example = _contract(name)
        invalid = copy.deepcopy(example)
        invalid[field] = {}
        assert not Draft202012Validator(schema).is_valid(invalid)


def test_s4_commitment_and_checkpoint_are_closed():
    commitment_schema, commitment = _contract("s4-commitment")
    checkpoint_schema, checkpoint = _contract("s4-checkpoint")

    commitment["task_uid"] = "T-001"
    checkpoint["execution_state"] = "pending"
    assert not Draft202012Validator(commitment_schema).is_valid(commitment)
    assert not Draft202012Validator(checkpoint_schema).is_valid(checkpoint)


def test_s4_state_is_closed_and_tracks_per_package_repairs():
    state_schema, state = _contract("s4-state")
    state["task_shard_repairs"] = {"wp-a": 1, "wp-b": 1}
    assert Draft202012Validator(state_schema).is_valid(state)
    invalid = copy.deepcopy(state)
    invalid["semantic_plan"] = {}
    assert not Draft202012Validator(state_schema).is_valid(invalid)


def test_s4_run_output_shape_has_typed_hash_anchors():
    schema = json.loads((SCHEMA_DIR / "run.schema.json").read_text(encoding="utf-8"))
    stage = {"status": "done", "started_at": None, "ended_at": None, "error": None}
    stage["output_refs"] = {
        "plan": {"path": "plan/versions/plan-1.0.0.json", "sha256": "0" * 64},
        "active_plan": {"path": "plan/active_plan.json", "sha256": "1" * 64},
        "delivery_blueprint_sha256": "2" * 64,
        "config_snapshot_sha256": "3" * 64,
    }
    run = json.loads((EXAMPLE_DIR / "run.example.json").read_text(encoding="utf-8"))
    run["stages"]["s4"] = stage
    assert Draft202012Validator(schema).is_valid(run)
    invalid = copy.deepcopy(run)
    invalid["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = {
        "path": "config.json",
        "sha256": "3" * 64,
    }
    assert not Draft202012Validator(schema).is_valid(invalid)
