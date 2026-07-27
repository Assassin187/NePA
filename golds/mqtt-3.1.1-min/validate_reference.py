"""Run D0.2 reference validation and archive all twenty pytest rounds."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GOLD_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tool_version(executable: str, *args: str) -> str:
    result = subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return (result.stdout + result.stderr).strip().splitlines()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--seed", type=int, default=311_000)
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=GOLD_ROOT / "validation" / "reference",
    )
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")

    mosquitto = shutil.which("mosquitto")
    if mosquitto is None:
        raise SystemExit("mosquitto broker is required for D0.2")
    if shutil.which("mosquitto_pub") is None or shutil.which("mosquitto_sub") is None:
        raise SystemExit("mosquitto client tools are required for D0.2")

    args.log_dir.mkdir(parents=True, exist_ok=True)
    tests = [
        str(GOLD_ROOT / "tests" / "l1_codec"),
        str(GOLD_ROOT / "tests" / "l2_behavior"),
    ]
    records: list[dict[str, Any]] = []
    for index in range(1, args.rounds + 1):
        seed = args.seed + index - 1
        command = [
            sys.executable,
            "-m",
            "pytest",
            *tests,
            "--target=reference",
            f"--seed={seed}",
            "-q",
        ]
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        log_path = args.log_dir / f"round_{index:02d}.log"
        log_path.write_text(
            f"$ {' '.join(command)}\n\n[stdout]\n{result.stdout}\n[stderr]\n{result.stderr}",
            encoding="utf-8",
        )
        records.append(
            {
                "round": index,
                "seed": seed,
                "exit_code": result.returncode,
                "log": log_path.name,
            }
        )
        print(f"round {index:02d}/{args.rounds}: exit={result.returncode}")
        if result.returncode != 0:
            break

    passed = len(records) == args.rounds and all(item["exit_code"] == 0 for item in records)
    summary = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "requested_rounds": args.rounds,
        "completed_rounds": len(records),
        "spec_sha256": _sha256(GOLD_ROOT / "spec" / "spec.json"),
        "tests_manifest_sha256": _sha256(GOLD_ROOT / "tests_manifest.json"),
        "environment": {
            "python": sys.version.split()[0],
            "pytest": importlib.metadata.version("pytest"),
            "paho_mqtt": importlib.metadata.version("paho-mqtt"),
            "mosquitto": _tool_version(mosquitto, "-h"),
        },
        "rounds": records,
    }
    (args.log_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
