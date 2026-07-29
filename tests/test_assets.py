from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from nepa.assets import (
    AssetValidationError,
    bundle_tree_sha256,
    publish_frozen_asset,
    resolve_profile_source,
    resolve_test_bundle_source,
    validate_profile,
    validate_test_bundle,
)
from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "nepa" / "schemas" / "examples"


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_fixture(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    bundle_root = tmp_path / "bundle"
    tests = bundle_root / "tests"
    tests.mkdir(parents=True)
    manifest = {
        "schema_version": "2.0",
        "tests": [
            {
                "nodeid": "tests/test_wire.py::test_wire",
                "description": "wire",
                "layer": "l1",
                "req_ids": ["REQ-WIRE-001"],
                "gate": "task",
                "required_contracts": ["codec-cli"],
                "build_variant_ids": ["san"],
            }
        ],
    }
    atomic_write_canonical_json(bundle_root / "tests_manifest.json", manifest)
    for name in ("runner.py", "oracle.py", "adapter.py"):
        (tests / name).write_text(f"# {name}\n", encoding="utf-8")
    value = _example("test-bundle.json")
    value["bundle_root"] = "bundle"
    value["manifest_ref"] = {
        "path": "bundle/tests_manifest.json",
        "sha256": _sha(bundle_root / "tests_manifest.json"),
        "schema_version": "2.0",
    }
    value["runner"]["entrypoint"] = "bundle/tests/runner.py"
    value["runner"]["sha256"] = _sha(tests / "runner.py")
    value["oracle_refs"] = [
        {
            "id": "oracle",
            "kind": "oracle",
            "ref": {
                "path": "bundle/tests/oracle.py",
                "sha256": _sha(tests / "oracle.py"),
            },
            "purpose": "oracle",
        }
    ]
    value["adapter_refs"] = [
        {
            "id": "adapter",
            "ref": {
                "path": "bundle/tests/adapter.py",
                "sha256": _sha(tests / "adapter.py"),
            },
            "contract_ids": ["codec-cli"],
            "purpose": "adapter",
        }
    ]
    value["reference_target_refs"] = []
    value["bundle_tree_sha256"] = bundle_tree_sha256(bundle_root)
    return value, bundle_root


def test_bundle_tree_hash_is_path_ordered_and_ignores_caches(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "b.txt").write_bytes(b"b")
    (root / "a.txt").write_bytes(b"a")
    first = bundle_tree_sha256(root)
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"ignored")
    assert bundle_tree_sha256(root) == first
    (root / "a.txt").write_bytes(b"changed")
    assert bundle_tree_sha256(root) != first


def test_validate_test_bundle_checks_manifest_components_and_tree(tmp_path: Path) -> None:
    value, bundle_root = _bundle_fixture(tmp_path)
    assert validate_test_bundle(value, workspace_root=tmp_path) == value

    changed = copy.deepcopy(value)
    changed["manifest_ref"]["sha256"] = "00" * 32
    with pytest.raises(AssetValidationError, match="manifest_ref"):
        validate_test_bundle(changed, workspace_root=tmp_path)

    (bundle_root / "tests" / "oracle.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(AssetValidationError, match="component ref"):
        validate_test_bundle(value, workspace_root=tmp_path)


def test_manifest_must_be_canonical(tmp_path: Path) -> None:
    value, bundle_root = _bundle_fixture(tmp_path)
    manifest_path = bundle_root / "tests_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    value["manifest_ref"]["sha256"] = _sha(manifest_path)
    value["bundle_tree_sha256"] = bundle_tree_sha256(bundle_root)
    with pytest.raises(AssetValidationError, match="canonical"):
        validate_test_bundle(value, workspace_root=tmp_path)


def test_profile_source_ref_uses_raw_file_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"source": true}\\n', encoding="utf-8")
    profile = _example("target-profile.json")
    profile["source_ref"] = {"path": "source.json", "sha256": _sha(source)}
    assert validate_profile(profile, kind="target", workspace_root=tmp_path) == profile
    source.write_text('{"source":false}\\n', encoding="utf-8")
    with pytest.raises(AssetValidationError, match="source_ref"):
        validate_profile(profile, kind="target", workspace_root=tmp_path)


def test_publish_frozen_asset_uses_canonical_bytes(tmp_path: Path) -> None:
    value = _example("architecture-draft.json")
    path = tmp_path / "architecture.json"
    ref = publish_frozen_asset(
        path,
        value,
        schema_name="architecture-draft.schema.json",
    )
    assert path.read_bytes() == canonical_json_bytes(value)
    assert ref["sha256"] == _sha(path)


def test_resolve_profile_source_injects_source_and_template_hashes(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "header.h").write_text("#define X 1\n", encoding="utf-8")
    source = _example("target-profile.json")
    del source["source_ref"]
    source["templates"][0]["path"] = "template"
    del source["templates"][0]["sha256"]
    source_path = tmp_path / "target.source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    resolved = resolve_profile_source(
        source_path,
        kind="target",
        workspace_root=tmp_path,
    )

    assert resolved["source_ref"]["sha256"] == _sha(source_path)
    assert resolved["templates"][0]["sha256"] == bundle_tree_sha256(template)


def test_resolve_test_bundle_source_injects_all_hashes(tmp_path: Path) -> None:
    value, bundle_root = _bundle_fixture(tmp_path)
    source = copy.deepcopy(value)
    del source["bundle_tree_sha256"]
    del source["manifest_ref"]["sha256"]
    del source["manifest_ref"]["schema_version"]
    del source["runner"]["sha256"]
    for collection in ("oracle_refs", "adapter_refs", "reference_target_refs"):
        for item in source[collection]:
            del item["ref"]["sha256"]
    source_path = tmp_path / "bundle.source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    resolved = resolve_test_bundle_source(source_path, workspace_root=tmp_path)

    assert resolved["bundle_tree_sha256"] == bundle_tree_sha256(bundle_root)
    assert resolved["manifest_ref"]["sha256"] == _sha(
        bundle_root / "tests_manifest.json"
    )
