"""沙箱构建工具（设计文档 6.5、7.4、8.5）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nepa.tools.sandbox import ExecResult, Sandbox


@dataclass(frozen=True)
class BuildResults:
    release: ExecResult
    sanitizer: ExecResult | None = None

    @property
    def ok(self) -> bool:
        return self.release.code == 0 and (
            self.sanitizer is None or self.sanitizer.code == 0
        )


class BuildTool:
    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def release(self, workspace: str | Path, timeout_s: int = 120) -> ExecResult:
        return self.sandbox.exec(["make"], str(workspace), timeout_s=timeout_s)

    def both(self, workspace: str | Path, timeout_s: int = 180) -> BuildResults:
        release = self.sandbox.exec(
            ["sh", "-lc", "make clean && make"],
            str(workspace),
            timeout_s=timeout_s,
        )
        if release.code != 0:
            return BuildResults(release=release)
        sanitizer = self.sandbox.exec(
            ["sh", "-lc", "make clean && make SAN=1"],
            str(workspace),
            timeout_s=timeout_s,
        )
        return BuildResults(release=release, sanitizer=sanitizer)
