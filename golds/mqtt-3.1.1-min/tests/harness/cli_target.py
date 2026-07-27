"""Map the frozen client CLI contract to generated or Mosquitto commands."""

from __future__ import annotations

import shutil
from pathlib import Path


def publish_command(
    target: str,
    workspace: Path | None,
    host: str,
    port: int,
    topic: str,
    message: str,
) -> list[str]:
    if target == "reference":
        executable = shutil.which("mosquitto_pub")
        if executable is None:
            raise RuntimeError("mosquitto_pub is not installed")
        return [executable, "-h", host, "-p", str(port), "-t", topic, "-m", message]
    if workspace is None:
        raise RuntimeError("--workspace is required for target=workspace")
    return [
        str(workspace / "build" / "mqtt_client_cli"),
        "pub",
        "--host",
        host,
        "--port",
        str(port),
        "--topic",
        topic,
        "--message",
        message,
    ]


def subscribe_command(
    target: str,
    workspace: Path | None,
    host: str,
    port: int,
    topic: str,
    count: int,
    timeout_s: int,
) -> list[str]:
    if target == "reference":
        executable = shutil.which("mosquitto_sub")
        if executable is None:
            raise RuntimeError("mosquitto_sub is not installed")
        return [
            executable,
            "-h",
            host,
            "-p",
            str(port),
            "-t",
            topic,
            "-C",
            str(count),
            "-W",
            str(timeout_s),
            "-F",
            "%t\t%p",
        ]
    if workspace is None:
        raise RuntimeError("--workspace is required for target=workspace")
    return [
        str(workspace / "build" / "mqtt_client_cli"),
        "sub",
        "--host",
        host,
        "--port",
        str(port),
        "--topic",
        topic,
        "--count",
        str(count),
        "--timeout",
        str(timeout_s),
    ]
