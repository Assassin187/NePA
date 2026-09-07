"""Generate byte-stable, protocol-neutral F1 lease fixture manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from nepa.speclib.lint import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
RECOVERY_WINDOWS = [
    "start",
    "response",
    "candidate",
    "results",
    "member_evidence",
    "joint_evidence",
    "wal",
    "install",
    "commit",
    "state",
    "file_ledger",
    "events",
]

METRIC_CASES = {
    "schema_version": "1.0",
    "cases": [
        {"id": "split-final-r0", "lineage": "initial A splits to A1/A2; B remains done", "expected": {"final_completion": 2 / 3, "r0_completion": 0.5, "final_blocked": 1 / 3, "r0_blocked": 0.5, "ever_blocked": 0.25}},
        {"id": "regenerated-success", "lineage": "A2 regenerated generation completes", "expected": {"final_completion": 1.0, "r0_completion": 1.0, "historical_blocking_preserved": True}},
        {"id": "first-pass", "lineage": "A first attempt; B second attempt; C regenerated", "expected": {"first_pass": 1 / 3, "first_pass_after_revision": 1.0}},
        {"id": "revision-lease", "lineage": "F2, RG-2, TR-5, two lease starts", "expected": {"f2": 1, "rejected": 1, "trigger": 1, "lease_success_rate": 0.5}},
    ],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(case_id: str) -> dict[str, Any]:
    s6 = ROOT / "tests/fixtures/s6" / case_id / "provider-sequence.json"
    source = ROOT / "tests/fixtures/s5" / case_id
    source_files = {
        f"tests/fixtures/s5/{case_id}/{path.relative_to(source).as_posix()}": _sha256(path)
        for path in sorted(source.rglob("*.json"), key=lambda item: item.relative_to(source).as_posix().encode("utf-8"))
    }
    return {
        "schema_version": "1.0",
        "fixture_id": f"s6-lease-{case_id}",
        "protocol": case_id,
        "provenance": {
            "provider_sequence": f"tests/fixtures/s6/{case_id}/provider-sequence.json",
            "provider_sequence_sha256": _sha256(s6),
            "s5_source_files": source_files,
        },
        "authorization_cases": [
            {"id": "eligible_success", "expected": "accepted", "external_files": 1, "lease_budget": 1},
            {"id": "validation_failure", "expected": "rejected", "reason": "UNSAFE_PATH"},
            {"id": "attempt_exhaustion", "expected": "rejected", "reason": "NO_FIXER_ATTEMPT"},
            {"id": "lease_budget_exhaustion", "expected": "rejected", "reason": "LEASE_CAP_EXHAUSTED"},
        ],
        "provider_sequences": ["success", "fail_then_fix", "four_failures", "dependency_and_independent"],
        "recovery_windows": RECOVERY_WINDOWS,
        "metric_lineage_fixture": "tests/fixtures/metrics/fixed-cases.json",
    }


def generate(output: Path) -> None:
    for case_id in ("mqtt", "non_mqtt"):
        destination = output / case_id / "lease-fixtures.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json_bytes(_fixture(case_id)))
    metrics = output / "metrics" / "fixed-cases.json"
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_bytes(canonical_json_bytes(METRIC_CASES))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output.resolve())
