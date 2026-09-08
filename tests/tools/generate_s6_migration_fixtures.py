"""Generate byte-stable M1-9 migration and repair-group fixture manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from nepa.speclib.lint import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"fixture input is not an object: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(case_id: str) -> dict[str, Any]:
    source = ROOT / "tests/fixtures/s5" / case_id
    provider_path = ROOT / "tests/fixtures/s6" / case_id / "provider-sequence.json"
    plan = _read(source / "plan.json")
    blueprint = _read(source / "blueprint.json")
    provider = _read(provider_path)
    tasks = sorted(plan["tasks"], key=lambda item: item["id"].encode("utf-8"))
    member_uids = sorted((task["task_uid"] for task in tasks), key=lambda value: value.encode("utf-8"))
    affected_paths = sorted(
        (path for task in tasks for path in task["deliverable_files"]),
        key=lambda value: value.encode("utf-8"),
    )
    first = tasks[0]
    return {
        "schema_version": "1.0",
        "fixture_id": f"s6-migration-{case_id}",
        "sources": {
            "plan": {"path": f"tests/fixtures/s5/{case_id}/plan.json", "sha256": _sha(source / "plan.json")},
            "blueprint": {"path": f"tests/fixtures/s5/{case_id}/blueprint.json", "sha256": _sha(source / "blueprint.json")},
            "provider_sequence": {"path": f"tests/fixtures/s6/{case_id}/provider-sequence.json", "sha256": _sha(provider_path)},
        },
        "f2": {
            "plan_version": "1.0.1", "epoch": "E0",
            "migration_ref": {"revision_seq": 1, "event_seq": 5},
            "scenarios": {
                "revalidate": {"task_id": first["id"], "attempt": 0, "role": None, "same_tree": True},
                "amend_after_four": {"task_id": first["id"], "ordinary_attempts": 4, "amendment_used": 1, "role": "fixer", "tier": "T1"},
                "regenerate": {"task_id": first["id"], "ordinary_attempts": 0, "first_role": "coder", "first_tier": "T2"},
            },
        },
        "f3": {
            "plan_version": "1.1.0", "epoch": "E1",
            "migration_ref": {"revision_seq": 1, "event_seq": 3},
            "group": {
                "group_id": "g-1-1", "member_task_uids": member_uids,
                "affected_paths": affected_paths, "affected_symbols": [],
                "build_artifact_ids": [blueprint["build_artifacts"][0]["id"]],
            },
            "provider_responses": {
                task["id"]: provider["responses"][f"success:{task['id']}"]
                for task in tasks
            },
        },
    }


def generate(output: Path) -> None:
    for case_id in ("mqtt", "non_mqtt"):
        destination = output / case_id / "migration-fixtures.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json_bytes(_case(case_id)) + b"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output.resolve())
