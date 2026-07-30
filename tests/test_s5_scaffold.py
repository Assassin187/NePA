"""S5 controller tests: materialization, receipts, and done no-op."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nepa.canonical import atomic_write_canonical_json
from nepa.stages.s4_plan import compile_plan
from nepa.stages.s5_scaffold import S5Error, S5Inputs, scaffold_project
from nepa.tools.build import BuildTool
from nepa.tools.fs_ops import sha256_file
from nepa.tools.sandbox import ExecResult
from tests.s4_stubs import build_harness, layered_queues

ROOT = Path(__file__).resolve().parent.parent


class _PassingSandbox:
    def exec(
        self,
        command: list[str],
        cwd: str,
        timeout_s: int,
        net: str = "none",
    ) -> ExecResult:
        del command, cwd, timeout_s, net
        return ExecResult(code=0, stdout="", stderr="", duration_ms=1, timed_out=False)


def _gate_runner(
    workspace: Path,
    tests: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    del workspace
    return [
        {
            "nodeid": item["nodeid"],
            "layer": item["layer"],
            "result": "pass",
            "duration_ms": 1,
            "req_ids": list(item["req_ids"]),
        }
        for item in tests
    ]


def _prepare_s5_harness(tmp_path: Path) -> tuple[Any, S5Inputs]:
    harness = build_harness(tmp_path, layered_queues())
    target = harness.inputs.target
    language = harness.inputs.language

    target_tree = tmp_path / "templates" / "targets" / "sample-layout"
    (target_tree / "include" / "proto").mkdir(parents=True)
    (target_tree / "README.md").write_text("# Sample\n", encoding="utf-8")
    (target_tree / "include" / "proto" / "codec.h").write_text(
        "#ifndef SAMPLE_CODEC_H\n#define SAMPLE_CODEC_H\n#endif\n",
        encoding="utf-8",
    )
    from nepa.assets import bundle_tree_sha256

    target["templates"][0]["path"] = target_tree.relative_to(tmp_path).as_posix()
    target["templates"][0]["sha256"] = bundle_tree_sha256(target_tree)

    language_template = tmp_path / "templates" / "languages" / "c99" / "Makefile.j2"
    language_template.parent.mkdir(parents=True)
    language_template.write_text(
        (ROOT / "profiles" / "templates" / "c99-posix" / "Makefile.j2").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    language["toolchain"]["build_file_template"] = {
        "path": language_template.relative_to(tmp_path).as_posix(),
        "sha256": sha256_file(language_template),
    }

    refs = dict(harness.inputs.input_refs)
    for kind, value in (("target_profile", target), ("language_profile", language)):
        path = harness.run_dir / refs[kind]["path"]
        atomic_write_canonical_json(path, value)
        refs[kind] = {"path": refs[kind]["path"], "sha256": sha256_file(path)}
    run_inputs = harness.store.read()["inputs"]
    for kind in ("target_profile", "language_profile"):
        run_inputs[kind]["path"] = refs[kind]["path"]
        run_inputs[kind]["sha256"] = refs[kind]["sha256"]
    harness.store.set_inputs(run_inputs)
    harness.inputs = replace(
        harness.inputs,
        target=target,
        language=language,
        input_refs=refs,
    )
    compile_plan(
        harness.store,
        harness.config,
        harness.inputs,
        harness.runner,
        harness.budget,
    )
    inputs = S5Inputs(
        spec=harness.inputs.spec,
        target=target,
        language=language,
        test_bundle=harness.inputs.test_bundle,
        manifest=harness.inputs.manifest,
        input_refs=refs,
        repo_root=tmp_path,
    )
    return harness, inputs


def test_s5_publishes_artifacts_summary_git_head_and_done_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness, inputs = _prepare_s5_harness(tmp_path)
    monkeypatch.setattr(
        "nepa.stages.s5_scaffold.validate_profile",
        lambda *args, **kwargs: args[0],
    )
    monkeypatch.setattr(
        "nepa.stages.s5_scaffold.validate_test_bundle",
        lambda *args, **kwargs: args[0],
    )
    result = scaffold_project(
        harness.store,
        inputs,
        build_tool=BuildTool(_PassingSandbox()),  # type: ignore[arg-type]
        gate_runner=_gate_runner,
    )

    assert result.published is True
    assert harness.store.meta.stages["s5"].status == "done"
    refs = harness.store.meta.stages["s5"].output_refs
    assert refs is not None
    assert refs["workspace_head"] == result.workspace_head
    assert (harness.run_dir / refs["artifact_manifest"]["path"]).is_file()
    assert (harness.run_dir / refs["contract_map"]["path"]).is_file()
    assert (harness.run_dir / refs["s5_summary"]["path"]).is_file()
    assert result.artifact_manifest["build_artifacts"]
    assert (harness.run_dir / "workspace" / "Makefile").is_file()

    repeated = scaffold_project(
        harness.store,
        inputs,
        build_tool=BuildTool(_PassingSandbox()),  # type: ignore[arg-type]
        gate_runner=_gate_runner,
    )
    assert repeated.published is False
    assert repeated.workspace_head == result.workspace_head


def test_s5_done_receipt_corruption_is_fail_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness, inputs = _prepare_s5_harness(tmp_path)
    monkeypatch.setattr(
        "nepa.stages.s5_scaffold.validate_profile",
        lambda *args, **kwargs: args[0],
    )
    monkeypatch.setattr(
        "nepa.stages.s5_scaffold.validate_test_bundle",
        lambda *args, **kwargs: args[0],
    )
    tool = BuildTool(_PassingSandbox())  # type: ignore[arg-type]
    scaffold_project(harness.store, inputs, build_tool=tool, gate_runner=_gate_runner)
    path = harness.run_dir / "plan" / "contract_map.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(S5Error, match="SCAFFOLD_RECEIPT_INVALID"):
        scaffold_project(harness.store, inputs, build_tool=tool, gate_runner=_gate_runner)
