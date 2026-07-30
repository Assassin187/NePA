"""S6 controller tests for task commits, evidence, and blocked propagation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nepa.stages.s5_scaffold import scaffold_project
from nepa.stages.s6_execute import S6Error, S6Inputs, execute_plan
from nepa.tools.build import BuildTool
from nepa.tools.sandbox import ExecResult
from tests.test_s5_scaffold import (
    _gate_runner,
    _PassingSandbox,
    _prepare_s5_harness,
)


class _CodeRunner:
    def invoke(
        self,
        role_name: str,
        payload: dict[str, Any],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del output_schema, kwargs
        if role_name == "diagnoser":
            return {
                "root_cause": "The candidate did not pass acceptance.",
                "suspect_files": list(payload["task"]["deliverable_files"]),
                "fix_guidance": "Regenerate the complete whitelisted files.",
            }
        task = payload["task"]
        files = []
        for relative in task["deliverable_files"]:
            if relative.endswith(".h"):
                content = "#ifndef TASK_HEADER_H\n#define TASK_HEADER_H\n#endif\n"
            elif relative.startswith("apps/"):
                content = (
                    "#include <stdlib.h>\n"
                    "int main(void) {\n"
                    "    return EXIT_SUCCESS;\n"
                    "}\n"
                )
            else:
                content = "/* Deterministic S6 test implementation. */\n"
            files.append({"path": relative, "content": content})
        return {
            "micro_plan": ["Implement the task-owned files."],
            "files": files,
            "notes": "accepted",
        }


class _FailingBuildSandbox:
    def exec(
        self,
        command: list[str],
        cwd: str,
        timeout_s: int,
        net: str = "none",
    ) -> ExecResult:
        del cwd, timeout_s, net
        joined = " ".join(command)
        if "make clean && make" in joined:
            return ExecResult(
                code=2,
                stdout="",
                stderr="compile failed",
                duration_ms=1,
                timed_out=False,
            )
        return ExecResult(code=0, stdout="", stderr="", duration_ms=1, timed_out=False)


def _install_asset_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nepa.stages.s5_scaffold.validate_profile",
        lambda *args, **kwargs: args[0],
    )
    monkeypatch.setattr(
        "nepa.stages.s5_scaffold.validate_test_bundle",
        lambda *args, **kwargs: args[0],
    )


def test_s6_commits_each_task_with_evidence_and_seals_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness, s5_inputs = _prepare_s5_harness(tmp_path)
    _install_asset_stubs(monkeypatch)
    scaffold_project(
        harness.store,
        s5_inputs,
        build_tool=BuildTool(_PassingSandbox()),  # type: ignore[arg-type]
        gate_runner=_gate_runner,
    )
    result = execute_plan(
        harness.store,
        S6Inputs(
            spec=s5_inputs.spec,
            language=s5_inputs.language,
            test_bundle=s5_inputs.test_bundle,
            manifest=s5_inputs.manifest,
        ),
        _CodeRunner(),  # type: ignore[arg-type]
        build_tool=BuildTool(_PassingSandbox()),  # type: ignore[arg-type]
        gate_runner=_gate_runner,
    )

    assert result.published is True
    assert all(item["status"] == "done" for item in result.state["tasks"])
    assert harness.store.meta.stages["s6"].status == "done"
    refs = harness.store.meta.stages["s6"].output_refs
    assert refs is not None
    assert refs["workspace_head"] == result.workspace_head
    for item in result.state["tasks"]:
        assert item["commit_sha"]
        evidence = harness.run_dir / item["acceptance_evidence"]["task_evidence_ref"]["path"]
        assert evidence.is_file()

    repeated = execute_plan(
        harness.store,
        S6Inputs(
            spec=s5_inputs.spec,
            language=s5_inputs.language,
            test_bundle=s5_inputs.test_bundle,
            manifest=s5_inputs.manifest,
        ),
        _CodeRunner(),  # type: ignore[arg-type]
        build_tool=BuildTool(_PassingSandbox()),  # type: ignore[arg-type]
        gate_runner=_gate_runner,
    )
    assert repeated.published is False


def test_s6_exhaustion_blocks_task_and_dependency_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness, s5_inputs = _prepare_s5_harness(tmp_path)
    _install_asset_stubs(monkeypatch)
    scaffold_project(
        harness.store,
        s5_inputs,
        build_tool=BuildTool(_PassingSandbox()),  # type: ignore[arg-type]
        gate_runner=_gate_runner,
    )
    with pytest.raises(S6Error, match="EXECUTION_TASKS_BLOCKED"):
        execute_plan(
            harness.store,
            S6Inputs(
                spec=s5_inputs.spec,
                language=s5_inputs.language,
                test_bundle=s5_inputs.test_bundle,
                manifest=s5_inputs.manifest,
            ),
            _CodeRunner(),  # type: ignore[arg-type]
            build_tool=BuildTool(_FailingBuildSandbox()),  # type: ignore[arg-type]
            gate_runner=_gate_runner,
        )

    state = json.loads(
        (harness.run_dir / "plan" / "plan_state.json").read_text(encoding="utf-8")
    )
    statuses = {item["id"]: item["status"] for item in state["tasks"]}
    assert "blocked" in statuses.values()
    assert "blocked_by_dependency" in statuses.values()
