"""Deterministic build and smoke orchestration for a rendered E0 tree."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Protocol


class Executor(Protocol):
    def exec(self, cmd: list[str], cwd: str, timeout_s: int, net: str = "none") -> Any: ...


def _tree_sha256(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts), key=lambda item: item.relative_to(workspace).as_posix().encode("utf-8")):
        relative = path.relative_to(workspace).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big")); digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return digest.hexdigest()


def _variants(blueprint: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[str]:
    configured = constraints.get("build_variant_ids") or constraints.get("default_build_variant_ids") or ["release", "san"]
    declared = {variant for artifact in blueprint.get("build_artifacts", []) for variant in artifact.get("build_variant_ids", [])}
    values = [str(value) for value in configured if not declared or value in declared]
    if not values:
        values = ["release", "san"]
    return sorted(set(values), key=lambda value: value.encode("utf-8"))


def _result(value: Any, *, variant: str, command: list[str], tree_sha: str, artifacts: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "tree_sha256": tree_sha, "variant": variant,
        "command": command, "exit_code": getattr(value, "returncode", None),
        "stdout": str(getattr(value, "stdout", "")), "stderr": str(getattr(value, "stderr", "")),
        "duration_ms": int(getattr(value, "duration_ms", 0)), "timed_out": bool(getattr(value, "timed_out", False)),
        "artifacts": artifacts, "status": "passed" if getattr(value, "returncode", None) == 0 and not getattr(value, "timed_out", False) else "failed",
    }


def run_build_variants(executor: Executor, workspace: str | Path, blueprint: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = Path(workspace).resolve()
    artifacts = [str(item["path"]) for item in blueprint.get("build_artifacts", [])]
    results: list[dict[str, Any]] = []
    for variant in _variants(blueprint, constraints):
        clean_before = executor.exec(["make", "clean"], str(root), timeout_s=60, net="none")
        if getattr(clean_before, "returncode", None) != 0 or getattr(clean_before, "timed_out", False):
            raise RuntimeError(f"sandbox clean failed before {variant}")
        tree_sha = _tree_sha256(root)
        command = ["make", variant]
        try:
            result = executor.exec(command, str(root), timeout_s=300, net="none")
            record = _result(result, variant=variant, command=command, tree_sha=tree_sha, artifacts=artifacts)
            results.append(record)
        finally:
            clean_after = executor.exec(["make", "clean"], str(root), timeout_s=60, net="none")
            if getattr(clean_after, "returncode", None) != 0 or getattr(clean_after, "timed_out", False):
                raise RuntimeError(f"sandbox clean failed after {variant}")
        if record["status"] != "passed":
            raise RuntimeError(f"sandbox build failed for {variant}")
    return results


_SMOKE_SCRIPT = r'''import json, os, signal, subprocess, sys, time
artifact, dwell, grace = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
started = time.monotonic()
child_env = os.environ.copy()
child_env.setdefault("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=1")
child_env.setdefault("UBSAN_OPTIONS", "halt_on_error=1")
child = subprocess.Popen([artifact], start_new_session=True, env=child_env)
try:
    child.wait(timeout=dwell)
    state = "early_exit"
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=grace)
        state = "terminated"
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        state = "killed"
rc = child.returncode
observed = 143 if state == "terminated" else (128 - rc if rc < 0 else rc)
print("NEPA_SMOKE " + json.dumps({"state": state, "exit_code": observed, "duration_ms": int((time.monotonic()-started)*1000)}))
sys.exit(0)
'''


def _smoke_observation(stdout: str, fallback: int | None) -> tuple[str, int | None]:
    matches = re.findall(r"NEPA_SMOKE (\{.*\})", stdout)
    if not matches:
        return "missing", fallback
    try:
        value = json.loads(matches[-1])
    except json.JSONDecodeError:
        return "invalid", fallback
    return str(value.get("state", "invalid")), value.get("exit_code")


def run_smoke_checks(executor: Executor, workspace: str | Path, blueprint: Mapping[str, Any], variants: list[Mapping[str, Any]], dwell_seconds: int, term_grace_seconds: int) -> list[dict[str, Any]]:
    if dwell_seconds <= 0 or term_grace_seconds <= 0:
        raise ValueError("smoke dwell and grace must be positive")
    root = Path(workspace).resolve()
    tree_sha = _tree_sha256(root)
    records: list[dict[str, Any]] = []
    for variant in variants:
        variant_id = str(variant["variant"])
        for artifact in blueprint.get("build_artifacts", []):
            path = str(artifact["path"])
            executable = path if path.startswith("/") or path.startswith("./") else f"./{path}"
            command = ["python3", "-c", _SMOKE_SCRIPT, executable, str(dwell_seconds), str(term_grace_seconds)]
            clean_before = executor.exec(["make", "clean"], str(root), timeout_s=60, net="none")
            if getattr(clean_before, "returncode", None) != 0 or getattr(clean_before, "timed_out", False):
                raise RuntimeError(f"sandbox clean failed before smoke {variant_id}")
            try:
                build = executor.exec(["make", variant_id], str(root), timeout_s=300, net="none")
                if getattr(build, "returncode", None) != 0 or getattr(build, "timed_out", False):
                    raise RuntimeError(f"sandbox build failed before smoke {variant_id}")
                value = executor.exec(command, str(root), timeout_s=dwell_seconds + term_grace_seconds + 30, net="none")
                state, observed = _smoke_observation(str(getattr(value, "stdout", "")), getattr(value, "returncode", None))
                combined = f"{getattr(value, 'stdout', '')}\n{getattr(value, 'stderr', '')}".lower()
                sanitizer_clean = not any(token in combined for token in ("addresssanitizer", "undefinedbehaviorsanitizer", "runtime error:"))
                passed = state == "terminated" and observed == 143 and not getattr(value, "timed_out", False) and sanitizer_clean
                records.append({
                    "schema_version": "1.0", "tree_sha256": tree_sha, "variant": variant_id,
                    "artifact": path, "command": command, "dwell_seconds": dwell_seconds,
                    "term_grace_seconds": term_grace_seconds, "observed_exit_code": observed,
                    "normalized_exit_code": 143 if observed == 143 else observed,
                    "stdout": str(getattr(value, "stdout", "")), "stderr": str(getattr(value, "stderr", "")),
                    "timed_out": bool(getattr(value, "timed_out", False)), "sanitizer_clean": sanitizer_clean,
                    "status": "passed" if passed else "failed",
                })
                if not passed:
                    raise RuntimeError(f"sandbox smoke failed for {variant_id}:{path}")
            finally:
                clean_after = executor.exec(["make", "clean"], str(root), timeout_s=60, net="none")
                if getattr(clean_after, "returncode", None) != 0 or getattr(clean_after, "timed_out", False):
                    raise RuntimeError(f"sandbox clean failed after smoke {variant_id}")
    return records


__all__ = ["run_build_variants", "run_smoke_checks"]
