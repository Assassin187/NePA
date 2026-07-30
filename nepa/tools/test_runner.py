"""Execute selected Test Manifest nodes through a frozen pytest Test Bundle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nepa.tools.sandbox import Sandbox


class TestRunnerError(RuntimeError):
    """The frozen runner or workspace cannot be executed safely."""


class PytestGateRunner:
    """Run manifest-selected pytest nodes one by one inside the sandbox."""

    def __init__(
        self,
        sandbox: Sandbox,
        *,
        repo_root: str | Path,
        test_bundle: dict[str, Any],
    ) -> None:
        self.sandbox = sandbox
        self.repo_root = Path(repo_root).resolve()
        self.test_bundle = test_bundle

    def __call__(
        self,
        workspace: Path,
        tests: tuple[dict[str, Any], ...],
    ) -> list[dict[str, Any]]:
        workspace = workspace.resolve()
        if not workspace.is_relative_to(self.repo_root):
            raise TestRunnerError("generated workspace must be inside repo_root")
        runner = self.test_bundle["runner"]
        if runner.get("kind") != "pytest":
            raise TestRunnerError(f"unsupported Test Bundle runner: {runner.get('kind')}")
        container_workspace = (
            Path("/w") / workspace.relative_to(self.repo_root)
        ).as_posix()
        prefix = [str(value) for value in runner["command_prefix"]]
        cases: list[dict[str, Any]] = []
        for test in tests:
            command = [
                *prefix,
                str(test["nodeid"]),
                "--target=generated",
                f"--workspace={container_workspace}",
                "-q",
            ]
            result = self.sandbox.exec(
                command,
                str(self.repo_root),
                timeout_s=180,
            )
            if result.timed_out:
                status = "error"
            elif result.code == 0:
                status = "skipped" if " skipped" in result.stdout else "pass"
            else:
                status = "fail"
            case: dict[str, Any] = {
                "nodeid": test["nodeid"],
                "layer": test["layer"],
                "result": status,
                "duration_ms": result.duration_ms,
                "req_ids": list(test["req_ids"]),
            }
            if status != "pass":
                case["output_excerpt"] = (
                    result.stderr or result.stdout or "test runner failed"
                )[-4000:]
            cases.append(case)
        return cases
