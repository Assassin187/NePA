"""M0 gold asset integrity and drift tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from nepa.speclib.lint import spec_lint

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "golds" / "mqtt-3.1.1-min"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_gold_spec_passes_gold_lint() -> None:
    spec = _json(GOLD / "spec" / "spec.json")
    manifest = _json(GOLD / "tests_manifest.json")
    report = spec_lint(spec, tests_manifest=manifest["tests"], gold_mode=True)
    assert report.errors == []


def test_tests_manifest_validates_and_has_no_unknown_requirements() -> None:
    manifest = _json(GOLD / "tests_manifest.json")
    schema = _json(ROOT / "nepa" / "schemas" / "tests-manifest.schema.json")
    assert list(Draft202012Validator(schema).iter_errors(manifest)) == []
    req_ids = {item["id"] for item in _json(GOLD / "spec" / "spec.json")["requirements"]}
    assert manifest["tests"]
    assert all(item["req_ids"] for item in manifest["tests"])
    assert {req_id for item in manifest["tests"] for req_id in item["req_ids"]} <= req_ids


def test_tests_manifest_matches_collection(tmp_path: Path) -> None:
    generated = tmp_path / "tests_manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            str(GOLD / "collect_manifest.py"),
            "--output",
            str(generated),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json(generated) == _json(GOLD / "tests_manifest.json")


def test_legacy_three_file_drafts_are_archived() -> None:
    assert not list((ROOT / "schemas").glob("*"))
    assert not list((ROOT / "gold_specs").glob("*"))
    for path in (
        "legacy/schemas/generation-profile.schema.json",
        "legacy/schemas/specs-requirements.schema.json",
        "legacy/schemas/wire-format.schema.json",
        "legacy/gold_specs/mqtt-3.1.1-min-profile.json",
        "legacy/gold_specs/mqtt-3.1.1-min-requirements.json",
        "legacy/gold_specs/mqtt-3.1.1-wire-format.json",
        "legacy/migration-to-spec-ir-v2.md",
    ):
        assert (ROOT / path).is_file(), path
