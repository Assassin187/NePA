from __future__ import annotations

from pathlib import Path
import io
import subprocess

import pytest

from nepa.tools.sandbox import SandboxExecutor
import nepa.tools.sandbox as sandbox_module


def test_sandbox_command_is_network_disabled_and_workspace_confined(tmp_path: Path):
    executor = SandboxExecutor("nepa-sandbox:latest", 2, 4)
    command = executor.command_vector(["make", "release"], str(tmp_path))
    assert command[0:5] == ["docker", "run", "--rm", "--network", "none"]
    assert "-v" in command
    assert f"{tmp_path.resolve()}:/workspace:rw" in command
    with pytest.raises(ValueError):
        executor.command_vector(["make"], str(tmp_path), net="loopback")
    with pytest.raises(ValueError):
        executor.command_vector(["make"], str(tmp_path / "missing"))


def test_sandbox_rejects_invalid_vectors_and_timeouts(tmp_path: Path):
    executor = SandboxExecutor("nepa-sandbox:latest", 2, 4)
    with pytest.raises(ValueError):
        executor.exec([], str(tmp_path), 1)
    with pytest.raises(ValueError):
        executor.exec(["make"], str(tmp_path), 0)


def test_sandbox_timeout_removes_the_exact_container(tmp_path: Path, monkeypatch):
    removed = []

    class Process:
        stdout = io.BytesIO(b"partial")
        stderr = io.BytesIO(b"")

        def __init__(self, command, **_kwargs):
            Path(command[command.index("--cidfile") + 1]).write_text("a" * 64, encoding="ascii")
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("docker", timeout)
            return -9

        def kill(self):
            return None

    def run(command, **_kwargs):
        removed.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(sandbox_module.subprocess, "Popen", Process)
    monkeypatch.setattr(sandbox_module.subprocess, "run", run)
    result = SandboxExecutor("nepa-sandbox:latest", 2, 4).exec(["make"], str(tmp_path), 1)
    assert result.timed_out and result.observation == "timeout-cleaned"
    assert removed == [["docker", "rm", "-f", "a" * 64]]
