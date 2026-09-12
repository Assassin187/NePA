"""Opt-in paid acceptance. Never replace providers or seed generated projects."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid

import pytest

from nepa.config import load_config, public_config_snapshot
from nepa.run_store import atomic_json, runtime_fingerprint, tree_hashes
from nepa.speclib.lint import digest
from nepa.tools.build import BuildRunner
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner

pytestmark = pytest.mark.live_e2e
ROOT = Path(__file__).parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def test_three_independent_real_generations():
    if os.environ.get("NEPA_LIVE_E2E") != "1":
        pytest.skip("paid real-API acceptance requires NEPA_LIVE_E2E=1")
    assert not git("status", "--porcelain"), "freeze and commit code/prompts/config before formal acceptance"
    config = load_config(ROOT / "configs/default.yaml")
    assert os.environ.get(config.providers[config.coder.provider].api_key_env or ""), "configured API credential missing"
    runs_root = ROOT / "runs/e2e"  # Includes every debug/failed call in this campaign's budget.
    runs_root.mkdir(parents=True, exist_ok=True)
    batch = runs_root / "_acceptance" / uuid.uuid4().hex
    batch.mkdir(parents=True)
    frozen = {"commit": git("rev-parse", "HEAD"), "runtime": runtime_fingerprint()["package_sha256"],
              "config": digest(public_config_snapshot(config)),
              "inputs": {name: hashlib.sha256((ROOT / "gold_file" / name).read_bytes()).hexdigest()
                         for name in ("specIR.json", "target.json", "acceptance.json", "acceptance/mqtt_smoke.py")},
              "image": subprocess.check_output(["docker", "image", "inspect", config.sandbox.image, "--format", "{{.Id}}"], text=True).strip()}
    record = {"status": "running", "frozen": frozen, "runs": [], "scope": "configured minimum scenarios, not full conformance"}
    atomic_json(batch / "batch.json", record)
    executor = SandboxExecutor(config.sandbox.image, config.sandbox.cpu, config.sandbox.mem_gb)
    try:
        for index in range(3):
            assert git("rev-parse", "HEAD") == frozen["commit"]
            assert not git("status", "--porcelain")
            assert runtime_fingerprint()["package_sha256"] == frozen["runtime"]
            print(f"Starting real independent acceptance run {index + 1}/3; batch={batch}", flush=True)
            command = [sys.executable, "-m", "nepa", "run", "--spec", "gold_file/specIR.json",
                       "--target", "gold_file/target.json", "--acceptance", "gold_file/acceptance.json",
                       "--config", "configs/default.yaml", "--runs-root", str(runs_root)]
            try:
                process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    stdout, stderr = process.communicate(timeout=4 * 3600 + 600)
                except subprocess.TimeoutExpired:
                    process.send_signal(signal.SIGINT)
                    process.communicate(timeout=60)
                    raise
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                record["status"] = "failed"
                record["runs"].append({"index": index + 1, "error": "CLI exceeded run deadline; inspect retained run/cost evidence"})
                atomic_json(batch / "batch.json", record)
                raise
            row = {"index": index + 1, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
            record["runs"].append(row)
            atomic_json(batch / "batch.json", record)
            if result.returncode != 0:
                record["status"] = "failed"
                atomic_json(batch / "batch.json", record)
                pytest.fail(f"real generation failed (not excluded from campaign): {row}")
            status = json.loads(result.stdout)
            run_dir = Path(status["run_dir"])
            state = json.loads((run_dir / "run.json").read_bytes())
            report = json.loads((run_dir / "report.json").read_bytes())
            assert state["status"] == report["status"] == "success"
            assert state["runtime"]["package_sha256"] == frozen["runtime"]
            assert state["config_sha256"] == frozen["config"]
            assert state["sandbox_image"] == frozen["image"]
            assert len(state["tasks"]) >= 23 and all(t["status"] == "passed" for t in state["tasks"].values())
            assert len(report["requirements"]) == 110
            assert report["cache_hits"] == 0 and state["budget"]["calls"] > 0
            calls = [json.loads(path.read_bytes()) for path in sorted((run_dir / "evidence/calls").glob("*.response.json"))]
            assert calls and all(not call["response"]["cached"] for call in calls)
            assert all(call["response"]["provider_metadata"].get("provider") == config.coder.provider for call in calls)
            root_commit = subprocess.check_output(["git", "--git-dir", str(run_dir / "checkpoints.git"),
                                                  "rev-list", "--max-parents=0", state["accepted_checkpoint"]], text=True).strip()
            assert not subprocess.check_output(["git", "--git-dir", str(run_dir / "checkpoints.git"),
                                                "ls-tree", "-r", "--name-only", root_commit], text=True).strip()
            delivery = run_dir / state["delivery"]["path"]
            assert tree_hashes(delivery) == state["delivery"]["files"]
            rebuilt = batch / f"independent-rebuild-{index + 1}"
            shutil.copytree(delivery, rebuilt, symlinks=True)
            target = json.loads((run_dir / "inputs/target.json").read_bytes())
            acceptance = json.loads((run_dir / "inputs/acceptance.json").read_bytes())
            builds = BuildRunner(executor, config.sandbox.build_timeout_s).run(target, rebuilt, clean=True)
            checks = VerificationRunner(executor).run(target, acceptance, rebuilt, run_dir / "inputs/checks",
                                                      batch / f"independent-checks-{index + 1}")
            row.update({"run_id": state["run_id"], "budget": state["budget"], "builds": builds, "checks": checks,
                        "delivery": str(delivery), "tasks": len(state["tasks"])})
            atomic_json(batch / "batch.json", record)
            assert builds["passed"] and checks["passed"], row
            assert runtime_fingerprint()["package_sha256"] == frozen["runtime"]
    except BaseException as exc:
        record["status"] = "failed"
        record["failure"] = f"{type(exc).__name__}: {exc}"
        atomic_json(batch / "batch.json", record)
        raise
    record["status"] = "passed"
    record["statement"] = "Three real generations passed configured build/minimum interactions; other behavior is not fully verified."
    atomic_json(batch / "batch.json", record)
    print(f"Acceptance evidence: {batch / 'batch.json'}", flush=True)
