from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from nepa.speclib.lint import canonical_json_bytes


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/s6/lease"


def _load(case_id: str) -> dict:
    path = FIXTURES / case_id / "lease-fixtures.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert path.read_bytes() == canonical_json_bytes(value)
    return value


@pytest.mark.s6_lease
@pytest.mark.parametrize("case_id", ["mqtt", "non_mqtt"])
def test_frozen_lease_manifest_binds_protocol_fixture(case_id: str):
    value = _load(case_id)
    assert value["fixture_id"] == f"s6-lease-{case_id}"
    assert value["provider_sequences"] == ["success", "fail_then_fix", "four_failures", "dependency_and_independent"]
    assert set(value["recovery_windows"]) == {
        "start", "response", "candidate", "results", "member_evidence", "joint_evidence", "wal", "install", "commit", "state", "file_ledger", "events",
    }
    provider = ROOT / value["provenance"]["provider_sequence"]
    assert hashlib.sha256(provider.read_bytes()).hexdigest() == value["provenance"]["provider_sequence_sha256"]
    for relative, expected in value["provenance"]["s5_source_files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


@pytest.mark.s6_lease
def test_lease_fixture_generation_is_byte_stable(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    command = [sys.executable, str(ROOT / "tests/tools/generate_s6_lease_fixtures.py")]
    subprocess.run([*command, "--output", str(first)], cwd=ROOT, check=True)
    subprocess.run([*command, "--output", str(second)], cwd=ROOT, check=True)
    for case_id in ("mqtt", "non_mqtt"):
        committed = (FIXTURES / case_id / "lease-fixtures.json").read_bytes()
        assert (first / case_id / "lease-fixtures.json").read_bytes() == committed
        assert (second / case_id / "lease-fixtures.json").read_bytes() == committed
    committed_metrics = (ROOT / "tests/fixtures/metrics/fixed-cases.json").read_bytes()
    assert (first / "metrics" / "fixed-cases.json").read_bytes() == committed_metrics
    assert (second / "metrics" / "fixed-cases.json").read_bytes() == committed_metrics


@pytest.mark.metric_contract
def test_static_metric_lineage_fixture_is_canonical_and_complete():
    path = ROOT / "tests/fixtures/metrics/fixed-cases.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert path.read_bytes() == canonical_json_bytes(value)
    assert {case["id"] for case in value["cases"]} == {
        "split-final-r0", "regenerated-success", "first-pass", "revision-lease",
    }
