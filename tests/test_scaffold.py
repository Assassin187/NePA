from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from nepa.delivery import compile_delivery_constraints
from nepa.profile_build import build_default_assets
from nepa.scaffold import (
    ScaffoldError,
    materialize_language_build_file,
    materialize_target_templates,
)

ROOT = Path(__file__).resolve().parent.parent


def _inputs() -> tuple[dict, dict, dict]:
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
    return target, language, constraints


def test_s5_materializer_only_writes_declared_template_slots(tmp_path: Path) -> None:
    target, language, constraints = _inputs()
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
        context={
            "source_files": ["src/session/mqtt_session.c"],
            "target_names": ["build/mqtt_broker"],
        },
    )

    assert {path.relative_to(tmp_path).as_posix() for path in written} == {
        "README.md",
        "include/mqtt/mqtt_net.h",
        "include/mqtt/mqtt_session.h",
    }
    assert makefile == tmp_path / "Makefile"
    assert "src/session/mqtt_session.c" in makefile.read_text(encoding="utf-8")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Makefile", "README.md", "include"]


def test_s5_materializer_rejects_template_file_without_file_rule(tmp_path: Path) -> None:
    target, _, constraints = _inputs()
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
