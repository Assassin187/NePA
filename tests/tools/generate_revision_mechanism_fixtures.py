"""Generate byte-stable MQTT/non-MQTT M1-10 coverage fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from nepa.speclib.lint import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
TRIGGER_CASES = (
    ("TR-1", "F3", "multi_task_undefined_symbol"),
    ("TR-2", "F2", "blocked_unique_provider_ratio"),
    ("TR-3", "F1_then_F2", "repeated_foreign_owned_write"),
    ("TR-4", "F2", "truncation_or_lint_overflow"),
    ("TR-5", "record_only", "independent_cross_module_gap"),
    ("TR-6", "F2", "blocked_task_ratio"),
    ("TR-7", "F3_or_F4", "missing_blueprint_input"),
    ("TR-8", "submission_reject", "contract_export_drift"),
)
OPERATORS = (
    ("split_task", "F2", "renumber_and_partition"),
    ("merge_tasks", "F2", "lineage_and_partition"),
    ("move_responsibility", "F2", "unique_primary_owner"),
    ("move_file_owner", "F2", "same_package_owner"),
    ("rewrite_instructions", "F2", "guidance_only"),
    ("insert_task", "F2", "source_owner_partition"),
    ("reorder_dependency", "F2", "contract_proven_edge"),
    ("add_contract", "F3", "closed_provider_consumer"),
    ("extend_contract", "F3", "monotonic_export"),
    ("add_file_slot", "F3", "existing_module_slot"),
    ("add_work_package", "F3", "existing_module_partition"),
    ("move_file_across_wp", "F3", "same_module_migration"),
    ("retire_file_slot", "F3", "successor_and_quarantine"),
    ("re_adopt", "F3", "registered_quarantine_source"),
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"fixture input is not an object: {path}")
    return value


def _case(case_id: str) -> dict[str, Any]:
    source = ROOT / "tests/fixtures/s5" / case_id / "plan.json"
    plan = _read(source)
    return {
        "schema_version": "1.0",
        "fixture_id": f"revision-mechanism-{case_id}",
        "source_plan": {
            "path": f"tests/fixtures/s5/{case_id}/plan.json",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "schema_version": plan["schema_version"],
            "task_uids": sorted((task["task_uid"] for task in plan["tasks"]), key=lambda value: value.encode("utf-8")),
        },
        "trigger_cases": [
            {"code": code, "expected_route": route, "scenario": scenario}
            for code, route, scenario in TRIGGER_CASES
        ],
        "operator_cases": [
            {"operator": operator, "level": level, "scenario": scenario}
            for operator, level, scenario in OPERATORS
        ],
        "required_cross_level_cases": [
            "f3_with_same_delta_f2_companion",
            "retire_add_explicit_path_migration",
            "f2_task_renumbering",
            "f2_owner_change",
            "f3_interface_extension",
            "f3_slot_retirement_re_adoption",
        ],
    }


def generate(output: Path) -> None:
    for case_id in ("mqtt", "non_mqtt"):
        destination = output / case_id / "revision-mechanism-fixtures.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json_bytes(_case(case_id)) + b"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output.resolve())
