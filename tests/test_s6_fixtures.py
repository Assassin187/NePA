from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from nepa.agents.s6 import validate_coding_response
from nepa.speclib.lint import canonical_json_bytes


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/s6"


def _load(case_id: str) -> dict:
    path = FIXTURES / case_id / "provider-sequence.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert path.read_bytes() == canonical_json_bytes(value)
    return value


def test_frozen_s6_provider_sequences_bind_archived_s5_inputs():
    for case_id in ("mqtt", "non_mqtt"):
        value = _load(case_id)
        assert value["fixture_id"] == f"s6-{case_id}"
        source = ROOT / value["source_fixture"]
        for relative, expected in value["source_files"].items():
            assert hashlib.sha256((source / relative).read_bytes()).hexdigest() == expected
        assert set(value["sequences"]) == {"success", "fail_then_fix", "four_failures", "dependency_and_independent"}
        responses = value["responses"]
        for response in responses.values():
            validate_coding_response(response)
        for sequence in (value["sequences"]["success"], value["sequences"]["fail_then_fix"], value["sequences"]["four_failures"]):
            assert all(item["response"] in responses for item in sequence)
        assert all(item["response"] in responses for item in value["sequences"]["dependency_and_independent"]["calls"])


def test_s6_fixture_generation_is_byte_stable(tmp_path):
    output = tmp_path / "generated"
    subprocess.run([sys.executable, str(ROOT / "tests/tools/generate_s6_fixtures.py"), "--output", str(output)], cwd=ROOT, check=True)
    for case_id in ("mqtt", "non_mqtt"):
        assert (output / case_id / "provider-sequence.json").read_bytes() == (FIXTURES / case_id / "provider-sequence.json").read_bytes()
