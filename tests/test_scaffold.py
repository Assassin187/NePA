from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from nepa.delivery import compile_delivery_constraints
from nepa.profile_build import build_default_assets
from nepa.scaffold import (
    ScaffoldError,
    materialize_language_build_file,
    materialize_mechanical_files,
    materialize_stubs,
    materialize_target_templates,
)

ROOT = Path(__file__).resolve().parent.parent


def _inputs() -> tuple[dict, dict, dict, dict]:
    target_path, language_path, bundle_path = build_default_assets(ROOT)
    target = json.loads(target_path.read_text(encoding="utf-8"))
    language = json.loads(language_path.read_text(encoding="utf-8"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    spec = json.loads(
        (ROOT / "golds" / "mqtt-3.1.1-min" / "spec" / "spec.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        (ROOT / "golds" / "mqtt-3.1.1-min" / "tests_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    constraints = compile_delivery_constraints(spec, target, language, bundle, manifest)
    return spec, target, language, constraints


def test_s5_materializer_only_writes_declared_template_slots(tmp_path: Path) -> None:
    _, target, language, constraints = _inputs()
    written = materialize_target_templates(
        tmp_path,
        workspace_root=ROOT,
        target=target,
        constraints=constraints,
    )
    makefile = materialize_language_build_file(
        tmp_path,
        workspace_root=ROOT,
        language=language,
        constraints=constraints,
    )

    assert {path.relative_to(tmp_path).as_posix() for path in written} == {
        "README.md",
        "include/mqtt/mqtt_net.h",
        "include/mqtt/mqtt_session.h",
    }
    assert makefile == tmp_path / "Makefile"
    assert "src/session/mqtt_session.c" in makefile.read_text(encoding="utf-8")
    assert "build/mqtt_broker" in makefile.read_text(encoding="utf-8")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Makefile", "README.md", "include"]


def test_s5_materializer_rejects_template_file_without_file_rule(tmp_path: Path) -> None:
    _, target, _, constraints = _inputs()
    source_root = ROOT / target["templates"][0]["path"]
    extra = source_root / "undeclared.txt"
    extra.write_text("must not leak\n", encoding="utf-8")
    changed = copy.deepcopy(target)
    from nepa.assets import bundle_tree_sha256

    changed["templates"][0]["sha256"] = bundle_tree_sha256(source_root)
    try:
        with pytest.raises(ScaffoldError, match="no matching file_rule"):
            materialize_target_templates(
                tmp_path,
                workspace_root=ROOT,
                target=changed,
                constraints=constraints,
            )
    finally:
        extra.unlink()


def test_s5_materializes_mechanical_contracts_and_buildable_stubs(
    tmp_path: Path,
) -> None:
    spec, target, language, constraints = _inputs()
    materialize_target_templates(
        tmp_path,
        workspace_root=ROOT,
        target=target,
        constraints=constraints,
    )
    mechanical = materialize_mechanical_files(
        tmp_path,
        workspace_root=ROOT,
        spec=spec,
        target=target,
        language=language,
        constraints=constraints,
    )
    stubs = materialize_stubs(tmp_path, constraints=constraints)
    materialize_language_build_file(
        tmp_path,
        workspace_root=ROOT,
        language=language,
        constraints=constraints,
    )

    assert {path.relative_to(tmp_path).as_posix() for path in mechanical} == {
        "include/mqtt/mqtt_codec.h",
        "include/mqtt/mqtt_types.h",
    }
    assert len(stubs) == len(
        [
            item
            for item in constraints["file_slots"]
            if item["mutability"] == "s6_owned" and item["producer"] == "stub"
        ]
    )
    assert "mqtt_connect_t" in (
        tmp_path / "include" / "mqtt" / "mqtt_types.h"
    ).read_text(encoding="utf-8")
    result = subprocess.run(
        ["make"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
