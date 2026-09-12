"""Build actual generated projects according to immutable Target commands."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from typing import Any
from .sandbox import SandboxExecutor
from ..speclib.lint import safe_relative


class BuildRunner:
    def __init__(self, executor: SandboxExecutor, timeout_s: int = 180):
        self.executor = executor
        self.timeout_s = timeout_s

    def run(self, target: dict[str, Any], workspace: Path, *, clean: bool = False) -> dict[str, Any]:
        rows = []
        if clean:
            result = self.executor.exec(target["clean"], str(workspace), self.timeout_s)
            rows.append({"variant": "clean", "passed": result.returncode == 0, "execution": asdict(result)})
            if result.returncode != 0:
                return {"passed": False, "builds": rows}
        for build in target["builds"]:
            result = self.executor.exec(build["argv"], str(workspace), self.timeout_s)
            artifact = workspace / safe_relative(build["artifact"])
            exists = artifact.is_file() and artifact.resolve().is_relative_to(workspace.resolve())
            # Final clean-build logs must show the compiler flags actually requested.
            output = result.stdout + result.stderr
            missing = [flag for flag in build["required_flags"] if flag not in output] if clean else []
            rows.append({"variant": build["id"], "passed": result.returncode == 0 and exists and not missing,
                         "artifact": build["artifact"], "artifact_exists": exists, "missing_flags": missing,
                         "execution": asdict(result)})
        return {"passed": all(row["passed"] for row in rows), "builds": rows}
