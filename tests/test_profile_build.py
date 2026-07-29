from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from nepa.assets import validate_profile, validate_test_bundle
from nepa.canonical import canonical_json_bytes
from nepa.profile_build import build_default_assets

ROOT = Path(__file__).resolve().parent.parent


def test_default_assets_are_reproducible_and_validate() -> None:
    paths = build_default_assets(ROOT)
    first = [path.read_bytes() for path in paths]
    assert [path.read_bytes() for path in build_default_assets(ROOT)] == first
    values = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert validate_profile(values[0], kind="target", workspace_root=ROOT) == values[0]
    assert validate_profile(values[1], kind="language", workspace_root=ROOT) == values[1]
    assert validate_test_bundle(values[2], workspace_root=ROOT) == values[2]
    assert all(path.read_bytes() == canonical_json_bytes(value) for path, value in zip(paths, values))

    for schema_name, value in (
        ("target-profile.schema.json", values[0]),
        ("language-profile.schema.json", values[1]),
        ("test-bundle.schema.json", values[2]),
    ):
        schema = json.loads((ROOT / "nepa" / "schemas" / schema_name).read_text())
        assert not list(Draft202012Validator(schema).iter_errors(value))
