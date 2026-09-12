"""Network-disabled Docker execution for generated E0 workspaces."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ExecResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    observation: str


class SandboxExecutor:
    """Execute a command only inside the configured Docker sandbox."""

    def __init__(self, image: str, cpu: int, mem_gb: float, *, max_output_bytes: int = 1_000_000) -> None:
        if not image or cpu <= 0 or mem_gb <= 0 or max_output_bytes <= 0:
            raise ValueError("sandbox image, resources, and output bound must be positive")
        self.image = image
        self.cpu = cpu
        self.mem_gb = mem_gb
        self.max_output_bytes = max_output_bytes

    def command_vector(self, cmd: list[str], cwd: str, *, net: Literal["none", "loopback", "internal"] = "none", readonly: dict[str, Path] | None = None) -> list[str]:
        if net != "none":
            raise ValueError("only network=none is permitted")
        if not isinstance(cmd, list) or not cmd or any(not isinstance(part, str) or not part for part in cmd):
            raise ValueError("sandbox command must be a non-empty argv vector")
        workspace = Path(cwd).resolve()
        if not workspace.is_dir():
            raise ValueError("sandbox cwd must be an existing workspace directory")
        mounts = []
        for destination, source in (readonly or {}).items():
            if destination == "/workspace" or not destination.startswith("/"):
                raise ValueError("invalid read-only mount")
            mounts.extend(["-v", f"{source.resolve()}:{destination}:ro"])
        return [
            "docker", "run", "--rm", "--network", "none", "--init",
            "--cpus", str(self.cpu), "--memory", f"{self.mem_gb}g",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{workspace}:/workspace:rw", *mounts, "-w", "/workspace", self.image, *cmd,
        ]

    def exec(self, cmd: list[str], cwd: str, timeout_s: int, net: Literal["none", "loopback", "internal"] = "none", *, readonly: dict[str, Path] | None = None) -> ExecResult:
        if timeout_s <= 0:
            raise ValueError("sandbox timeout must be positive")
        base_command = self.command_vector(cmd, cwd, net=net, readonly=readonly)
        descriptor, cid_name = tempfile.mkstemp(prefix="nepa-cid-")
        os.close(descriptor)
        cid_path = Path(cid_name)
        cid_path.unlink()
        command = [*base_command[:2], "--cidfile", os.fspath(cid_path), *base_command[2:]]
        started = time.monotonic()
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        truncated = [False, False]

        def drain(stream: object, target: bytearray, index: int) -> None:
            while True:
                chunk = stream.read(65536)  # type: ignore[attr-defined]
                if not chunk:
                    return
                remaining = self.max_output_bytes - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated[index] = True

        try:
            process = subprocess.Popen(command, cwd=os.fspath(Path(cwd).resolve()), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(target=drain, args=(process.stdout, stdout_buffer, 0)),
                threading.Thread(target=drain, args=(process.stderr, stderr_buffer, 1)),
            ]
            for reader in readers:
                reader.start()
            try:
                returncode = process.wait(timeout=timeout_s)
                timed_out = False
            except BaseException as interruption:
                process.kill()
                process.wait()
                timed_out = True
                returncode = None
                cid = cid_path.read_text(encoding="ascii").strip() if cid_path.exists() else ""
                if cid:
                    cleanup = subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)
                    if cleanup.returncode != 0:
                        raise RuntimeError("timed-out sandbox container could not be removed")
                if not isinstance(interruption, subprocess.TimeoutExpired):
                    for reader in readers:
                        reader.join()
                    raise
            for reader in readers:
                reader.join()
            observation = "timeout-cleaned" if timed_out else "completed"
            if any(truncated):
                observation += "-output-truncated"
            stdout = stdout_buffer.decode("utf-8", errors="replace")
            stderr = stderr_buffer.decode("utf-8", errors="replace")
        except OSError:
            raise
        finally:
            cid_path.unlink(missing_ok=True)
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecResult(command=command, returncode=returncode, stdout=stdout, stderr=stderr, duration_ms=duration_ms, timed_out=timed_out, observation=observation)


__all__ = ["ExecResult", "SandboxExecutor"]
