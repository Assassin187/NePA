"""L0 external build-contract tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.gate("s5"),
    pytest.mark.contract("build-system"),
    pytest.mark.build_variant("release"),
    pytest.mark.build_variant("san"),
]


@pytest.mark.req("REQ-FRAME-001")
def test_release_and_sanitizer_builds(target: str, workspace: Path | None) -> None:
    """Generated workspace builds release and sanitizer targets without warnings."""
    if target == "reference":
        pytest.skip("L0 is not applicable to the reference implementation")
    assert workspace is not None
    subprocess.run(["make", "clean"], cwd=workspace, check=True)
    subprocess.run(["make"], cwd=workspace, check=True)
    subprocess.run(["make", "clean"], cwd=workspace, check=True)
    subprocess.run(["make", "SAN=1"], cwd=workspace, check=True)


@pytest.mark.req("REQ-FRAME-001")
def test_required_binaries_exist(target: str, workspace: Path | None) -> None:
    """All binaries frozen by the 7.4 external contract exist."""
    if target == "reference":
        pytest.skip("L0 is not applicable to the reference implementation")
    assert workspace is not None
    for name in ("mqtt_broker", "mqtt_client_cli", "mqtt_codec_cli"):
        assert (workspace / "build" / name).is_file()
