from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan_revision import validate_file_ledger, validate_revision_ledger


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/s5"


def test_frozen_s5_fixtures_are_s4_published_and_canonical():
    for case_id in ("mqtt", "non_mqtt"):
        fixture = FIXTURES / case_id
        metadata = json.loads((fixture / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["fixture_id"] == case_id
        assert metadata["generated_via"].endswith("publish_initial_plan")
        for name in ("spec", "target", "test_bundle", "plan", "blueprint", "active_plan", "file_ledger", "revision_ledger", "constraints"):
            value = json.loads((fixture / f"{name}.json").read_text(encoding="utf-8"))
            assert (fixture / f"{name}.json").read_bytes() == canonical_json_bytes(value)
        active = json.loads((fixture / "active_plan.json").read_text(encoding="utf-8"))
        ledger = json.loads((fixture / "file_ledger.json").read_text(encoding="utf-8"))
        revision = json.loads((fixture / "revision_ledger.json").read_text(encoding="utf-8"))
        plan_sha = hashlib.sha256((fixture / "plan.json").read_bytes()).hexdigest()
        assert active["sha256"] == plan_sha
        assert metadata["s4_anchors"]["plan"]["sha256"] == plan_sha
        validate_file_ledger(ledger)
        validate_revision_ledger(revision)


def test_fixture_generator_replays_identical_semantic_bytes(tmp_path):
    generator = ROOT / "tests/tools/generate_s5_fixtures.py"
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output in (first, second):
        subprocess.run([sys.executable, str(generator), "--output", str(output)], cwd=ROOT, check=True)
    first_files = sorted(path.relative_to(first).as_posix() for path in first.rglob("*") if path.is_file())
    second_files = sorted(path.relative_to(second).as_posix() for path in second.rglob("*") if path.is_file())
    assert first_files == second_files
    assert {relative: (first / relative).read_bytes() for relative in first_files} == {relative: (second / relative).read_bytes() for relative in second_files}
