"""Start either the independent reference broker or a generated workspace broker."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Self


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Broker(AbstractContextManager["Broker"]):
    def __init__(self, target: str, workspace: Path | None) -> None:
        self.target = target
        self.workspace = workspace
        self.port = unused_port()
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> Self:
        if self.target == "reference":
            executable = shutil.which("mosquitto")
            if executable is None:
                raise RuntimeError("mosquitto broker is not installed")
            argv = [executable, "-p", str(self.port), "-v"]
            cwd = None
        else:
            if self.workspace is None:
                raise RuntimeError("--workspace is required for target=workspace")
            executable = self.workspace / "build" / "mqtt_broker"
            argv = [str(executable), "--port", str(self.port)]
            cwd = self.workspace
        self.process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                _out, err = self.process.communicate(timeout=1)
                raise RuntimeError(f"broker exited before ready: {err}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return self
            except OSError:
                time.sleep(0.05)
        self.__exit__(None, None, None)
        raise TimeoutError("broker readiness timeout")

    def connect(self, timeout: float = 2.0) -> socket.socket:
        return socket.create_connection(("127.0.0.1", self.port), timeout=timeout)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=1)
