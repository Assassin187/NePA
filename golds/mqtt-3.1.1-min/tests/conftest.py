"""Gold test configuration and randomized fixtures."""

from __future__ import annotations

import random
import string
import sys
from pathlib import Path
from typing import Any

import pytest

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("nepa-gold")
    group.addoption("--target", choices=("reference", "workspace"), default="reference")
    group.addoption("--workspace", type=Path)
    group.addoption("--seed", type=int, default=311)


@pytest.fixture
def target(request: pytest.FixtureRequest) -> str:
    return str(request.config.getoption("--target"))


@pytest.fixture
def workspace(request: pytest.FixtureRequest) -> Path | None:
    value: Any = request.config.getoption("--workspace")
    return Path(value).resolve() if value else None


@pytest.fixture
def broker(target: str, workspace: Path | None):
    from harness.target import Broker

    with Broker(target, workspace) as running:
        yield running


@pytest.fixture
def randomized(request: pytest.FixtureRequest) -> dict[str, Any]:
    seed = int(request.config.getoption("--seed"))
    rng = random.Random(seed)
    alphabet = string.ascii_lowercase + string.digits
    suffix = "".join(rng.choice(alphabet) for _ in range(10))
    values = {
        "seed": seed,
        "client_a": f"a{suffix[:8]}",
        "client_b": f"b{suffix[:8]}",
        "topic": f"nepa/{suffix}",
        "payload": f"payload-{suffix}".encode(),
    }
    print(f"GOLD_RANDOM_SEED={seed}")
    return values
