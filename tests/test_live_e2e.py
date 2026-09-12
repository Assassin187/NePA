"""Opt-in paid acceptance: prove one real run, then repeat twice independently."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from nepa.run_store import RunStore, atomic_json, runtime_fingerprint, tree_hashes
from nepa.speclib.lint import digest
from nepa.tools.build import BuildRunner
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner

pytestmark = pytest.mark.live_e2e
ROOT = Path(__file__).parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def fingerprint(config):
    return {"commit": git("rev-parse", "HEAD"), "runtime": runtime_fingerprint()["package_sha256"],
            "config": digest(public_config_snapshot(config)),
            "inputs": {name: hashlib.sha256((ROOT / "gold_file" / name).read_bytes()).hexdigest()
                       for name in ("specIR.json", "target.json", "acceptance.json", "acceptance/mqtt_smoke.py")},
            "image": subprocess.check_output(["docker", "image", "inspect", config.sandbox.image,
                                               "--format", "{{.Id}}"], text=True).strip()}


def launch(index, runs_root):
    command = [sys.executable, "-m", "nepa", "run", "--spec", "gold_file/specIR.json",
               "--target", "gold_file/target.json", "--acceptance", "gold_file/acceptance.json",
               "--config", "configs/default.yaml", "--runs-root", str(runs_root)]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=4 * 3600 + 600)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=60)
        raise
    return {"index": index, "returncode": process.returncode, "stdout": stdout, "stderr": stderr}


def verify_row(row, config, frozen, batch):
    assert row["returncode"] == 0, f"real generation failed (charged to campaign): {row}"
    status = json.loads(row["stdout"])
    run_dir = Path(status["run_dir"])
    state = json.loads((run_dir / "run.json").read_bytes())
    report = json.loads((run_dir / "report.json").read_bytes())
    assert state["status"] == report["status"] == "success"
    assert state["runtime"]["package_sha256"] == frozen["runtime"]
    assert state["config_sha256"] == frozen["config"]
    assert state["sandbox_image"] == frozen["image"]
    for source, snapshot in (("specIR.json", "spec.json"), ("target.json", "target.json"),
                             ("acceptance.json", "acceptance.json"),
                             ("acceptance/mqtt_smoke.py", "checks/acceptance/mqtt_smoke.py")):
        assert hashlib.sha256((run_dir / "inputs" / snapshot).read_bytes()).hexdigest() == frozen["inputs"][source]
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
    rebuilt = batch / f"independent-rebuild-{row['index']}"
    shutil.copytree(delivery, rebuilt, symlinks=True)
    target = json.loads((run_dir / "inputs/target.json").read_bytes())
    acceptance = json.loads((run_dir / "inputs/acceptance.json").read_bytes())
    executor = SandboxExecutor(config.sandbox.image, config.sandbox.cpu, config.sandbox.mem_gb)
    builds = BuildRunner(executor, config.sandbox.build_timeout_s).run(target, rebuilt, clean=True)
    checks = VerificationRunner(executor).run(target, acceptance, rebuilt, run_dir / "inputs/checks",
                                              batch / f"independent-checks-{row['index']}")
    row.update({"run_id": state["run_id"], "budget": state["budget"], "builds": builds, "checks": checks,
                "delivery": str(delivery), "tasks": len(state["tasks"])})
    assert builds["passed"] and checks["passed"], row


def run_batch(first_run_id=None):
    assert os.environ.get("NEPA_LIVE_E2E") == "1", "paid API requires explicit opt-in"
    config = load_config(ROOT / "configs/default.yaml")
    assert os.environ.get(config.providers[config.coder.provider].api_key_env or ""), "configured credential missing"
    frozen = fingerprint(config)
    assert not git("status", "--porcelain"), "freeze and commit before formal acceptance"
    harness_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runs_root = ROOT / "runs/e2e"  # All earlier paid experiments remain included.
    batch = runs_root / "_acceptance" / uuid.uuid4().hex
    batch.mkdir(parents=True)
    record = {"status": "running", "frozen": frozen, "harness_sha256": harness_sha,
              "scheduling": "one-success-then-two-parallel-repetitions", "phase": "first_generation",
              "first_run_continuation": first_run_id,
              "runs": [], "scope": "configured minimum scenarios, not full conformance"}
    atomic_json(batch / "batch.json", record)
    print(f"Starting first real end-to-end generation; batch={batch}", flush=True)
    def evaluate(index):
        row = {"index": index}
        try:
            assert fingerprint(config) == frozen and not git("status", "--porcelain")
            if index == 1 and first_run_id:
                store = RunStore.open(runs_root, first_run_id)
                assert store.run["status"] == "success" and store.run["exit_code"] == 0
                # User-authorized development continuation is explicitly marked;
                # all delivery checks below still run. Never alter its run state.
                row = {"index": index, "returncode": store.run["exit_code"],
                       "stdout": json.dumps({"run_dir": str(store.root)}), "stderr": "",
                       "configuration_changes": [entry for entry in store.run["history"]
                                                 if entry["kind"] == "configuration_change"]}
            else:
                row = launch(index, runs_root)
            verify_row(row, config, frozen, batch)
            assert fingerprint(config) == frozen
            assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == harness_sha
            row["passed"] = True
        except Exception as exc:
            row.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        return row
    first = evaluate(1)
    record["runs"].append(first)
    atomic_json(batch / "batch.json", record)
    if first["passed"]:
        record["phase"] = "stability_repetitions"
        atomic_json(batch / "batch.json", record)
        print(f"First run passed all independent checks; starting two repetitions; batch={batch}", flush=True)
        # Already-running repetitions finish independently even if one fails.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(evaluate, index) for index in (2, 3)]
            for future in as_completed(futures):
                row = future.result()
                record["runs"].append(row)
                atomic_json(batch / "batch.json", record)
                print(f"Run {row['index']} completed: passed={row['passed']}; batch={batch}", flush=True)
    record["runs"].sort(key=lambda row: row["index"])
    record["status"] = "passed" if len(record["runs"]) == 3 and all(row["passed"] for row in record["runs"]) else "failed"
    record["phase"] = "complete"
    record["statement"] = ("Three real generations passed configured build/minimum interactions; other behavior is not fully verified."
                           if record["status"] == "passed" else "One or more real runs failed; batch does not satisfy acceptance.")
    if first_run_id:
        record["statement"] += " First run is an explicitly resumed development run, not a fixed-candidate stability sample; repetitions are fresh projects."
    atomic_json(batch / "batch.json", record)
    return record


def test_three_independent_real_generations():
    if os.environ.get("NEPA_LIVE_E2E") != "1":
        pytest.skip("paid real-API acceptance requires NEPA_LIVE_E2E=1")
    assert run_batch(os.environ.get("NEPA_LIVE_FIRST_RUN"))["status"] == "passed"
