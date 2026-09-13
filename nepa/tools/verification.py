"""Host-owned private verification and the single public feedback projection."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
import threading
import uuid
from typing import Any

from .sandbox import SandboxExecutor
from .verification_worker import SANITIZER_MARKERS

VERIFIER_VERSION = "private-isolated/1"
RANDOMIZATION_VERSION = "random-inputs/1"


def _observation(value: Any) -> dict[str, Any]:
    """Only semantic fields from trusted oracles, never their raw result text."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"expected", "observed"} and isinstance(item, dict):
            result[key] = _observation(item)
        elif key in {"length", "expected_length", "observed_length", "count", "expected_count", "observed_count", "actual_length", "actual_count", "actual_status", "actual_type", "actual_order",
                     "expected_type", "expected_order", "offset", "expected_duration_ms", "actual_duration_ms",
                     "status_code", "expected_status", "observed_status", "exchange_count"}:
            if type(item) is int and 0 <= item <= 2**31:
                result[key] = item
        elif key in {"outcome", "type", "order", "expected_outcome", "observed_outcome", "expected_type", "observed_type",
                     "expected_order", "observed_order", "connection", "stage", "actual_outcome", "error_class"}:
            if isinstance(item, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item):
                result[key] = item
        elif key in {"closed", "timed_out", "matched", "ordered"} and type(item) is bool:
            result[key] = item
    return result


def _source_locations(text: str, workspace: Path) -> list[dict[str, Any]]:
    # A server can print a private vector disguised as a filename. Only existing
    # public C sources and their actual line ranges authorize a source location.
    root = workspace.resolve()
    locations = []
    for name, number in re.findall(r"/workspace/([A-Za-z0-9_./-]+\.(?:c|h)):(\d+)", text):
        path = root / name
        if ".." in Path(name).parts or not path.resolve().is_relative_to(root) or not path.is_file():
            continue
        try:
            lines = len(path.read_text().splitlines())
        except (OSError, UnicodeError):
            continue
        if 1 <= int(number) <= lines:
            locations.append({"path": name, "line": int(number)})
        if len(locations) >= 30:
            break
    return locations


def _verification_view(value: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for variant in value.get("variants", []):
        detail = variant.get("detail", {})
        # Raw locations are never trusted; derive from logs against the host-owned
        # public project. Re-projecting an already published diagnostic keeps its
        # previously validated positions (there are no raw server fields in it).
        if value.get("diagnostic_version") == "repair-diagnostic/1":
            locations = detail.get("source_locations", [])
        elif detail.get("project_root"):
            locations = _source_locations(detail.get("server_stdout", "") + detail.get("server_stderr", ""),
                                          Path(detail["project_root"]))
        else:
            locations = []
        rows.append({"variant": variant.get("variant"), "passed": variant.get("passed") is True,
                     "detail": {"passed": detail.get("passed") is True,
                                "server_returncode": detail.get("server_returncode"),
                                "early_exit": detail.get("early_exit"), "stop_timeout": detail.get("stop_timeout"),
                                "sanitizer_error": detail.get("sanitizer_error"),
                                "sanitizer_categories": [name for name in ("address", "undefined", "leak")
                                                         if name in detail.get("sanitizer_categories", [])],
                                "output_truncated": detail.get("output_truncated", False),
                                "source_locations": locations,
                                "server_output": "omitted",
                                "checks": [{"id": row["id"], "category": row.get("category", "checker_result_invalid"),
                                            "passed": row.get("passed") is True,
                                            "status": "passed" if row.get("passed") is True else "failed",
                                            "observation": _observation(row.get("observation"))}
                                           for row in detail.get("checks", [])]}})
    return {"diagnostic_version": "repair-diagnostic/1", "exposure": "private_isolated",
            "passed": value.get("passed") is True, "variants": rows}


def safe_feedback(value: dict[str, Any]) -> dict[str, Any]:
    """Recursively project raw host results before publication or model context use."""
    if "variants" in value:
        return _verification_view(value)
    if "checks" in value or "server_stdout" in value or "server_argv" in value:
        return _verification_view({"variants": [{"detail": value}]})["variants"][0]["detail"]
    if "argv" in value and ("req_ids" in value or "category" in value):
        return {"id": value.get("id"), "category": value.get("category", "checker_result_invalid"),
                "passed": value.get("passed") is True, "observation": _observation(value.get("observation"))}
    if "command" in value and "returncode" in value:
        command = value.get("command", [])
        # Strip the infrastructure invocation; only container output is public.
        if command[:2] == ["docker", "run"]:
            try:
                command = command[command.index("-w") + 3:]
            except ValueError:
                command = []
        result = {key: value[key] for key in ("returncode", "timed_out", "duration_ms", "observation") if key in value}
        result["command"] = command
        # Docker daemon diagnostics can include host bind paths; they are not compiler output.
        infrastructure_error = value.get("returncode") in {125, 126, 127} and any(
            marker in value.get("stderr", "") for marker in ("docker:", "Error response from daemon", "OCI runtime"))
        result["stdout"] = "" if infrastructure_error else value.get("stdout", "")
        result["stderr"] = "sandbox execution failed" if infrastructure_error else value.get("stderr", "")
        return result
    result = {}
    # Refs are published separately by the host, after projection; raw refs never survive it.
    omitted = {"evidence", "evidence_ref", "complete_result_ref", "seed", "port", "host", "argv", "server_argv",
               "server_stdout", "server_stderr", "trace", "trace_file", "raw_response", "source_locations", "project_root"}
    for key, item in value.items():
        if key in omitted or (key == "path" and "sha256" in value):
            continue
        if isinstance(item, dict):
            result[key] = safe_feedback(item)
        elif isinstance(item, list):
            result[key] = [safe_feedback(v) if isinstance(v, dict) else v for v in item]
        else:
            result[key] = item
    return result


class VerificationRunner:
    def __init__(self, executor: SandboxExecutor):
        self.executor = executor

    def run(self, target: dict[str, Any], acceptance: dict[str, Any], workspace: Path,
            checks_root: Path, evidence_dir: Path, *, seed: str | int | None = None) -> dict[str, Any]:
        from ..run_store import atomic_json
        evidence_dir.mkdir(parents=True, exist_ok=False)
        seed = secrets.token_hex(32) if seed is None else str(seed)
        atomic_json(evidence_dir / "attempt.json", {"seed": seed, "verifier_version": VERIFIER_VERSION,
                    "randomization_version": RANDOMIZATION_VERSION, "exposure": "private_isolated"})
        rows = []
        ports: set[int] = set()
        for build in target["builds"]:
            variant_seed = hashlib.sha256((seed + ":variant:" + build["id"]).encode()).hexdigest()
            port = 20000 + int(hashlib.sha256((variant_seed + ":port").encode()).hexdigest(), 16) % 40000
            while port in ports:
                port = 20000 + (port - 19999) % 40000
            ports.add(port)
            directory = evidence_dir / build["id"]
            directory.mkdir()
            checker_data = directory / "checker"
            checker_data.mkdir()
            payload = {"port": port, "seed": variant_seed, "checks": acceptance["checks"]}
            atomic_json(checker_data / "payload.json", payload)
            identifier = "nepa-verify-" + uuid.uuid4().hex
            ownership: dict[str, Any] = {"server": identifier + "-server", "checker": identifier + "-checker", "cleaned": False}
            # Names precede Docker I/O, so crash recovery covers create-before-ID publication too.
            atomic_json(directory / "containers.json", ownership)
            started = time.monotonic()
            detail: dict[str, Any] = {"passed": False, "checks": []}
            execution: dict[str, Any] = {"returncode": None, "timed_out": False}
            captures = []
            server_capture = None
            try:
                params = {"host": "127.0.0.1", "port": str(port), "artifact": "/workspace/" + build["artifact"]}
                server_argv = [part.format(**params) for part in target["run"]]
                ownership["server_id"] = self.executor.create_container(
                    ownership["server"], server_argv, network="none", readonly={"/workspace": workspace}, cwd="/workspace")
                atomic_json(directory / "containers.json", ownership)
                server_capture = self._capture(ownership["server"], directory, "server")
                captures.append(server_capture)
                startup_deadline = time.monotonic() + 15
                while not self._state(ownership["server"])["Running"]:
                    if server_capture[0].poll() is not None or time.monotonic() >= startup_deadline:
                        raise RuntimeError("server exited or failed to start")
                    time.sleep(.02)
                ownership["checker_id"] = self.executor.create_container(
                    ownership["checker"], ["python", "/trusted/verification_worker.py", "/verification/payload.json"],
                    network="container:" + ownership["server_id"],
                    readonly={"/checks": checks_root, "/trusted/verification_worker.py": Path(__file__).with_name("verification_worker.py")},
                    writable={"/verification": checker_data})
                atomic_json(directory / "containers.json", ownership)
                capture = self._capture(ownership["checker"], directory, "checker")
                captures.append(capture)
                process, readers, truncated = capture
                try:
                    execution["returncode"] = process.wait(timeout=sum(c["timeout_s"] for c in acceptance["checks"]) + 10)
                except BaseException as exc:
                    self.executor.remove_container(ownership["checker"])
                    process.kill()
                    process.wait()
                    if not isinstance(exc, subprocess.TimeoutExpired):
                        raise
                    execution["timed_out"] = True
                for reader in readers:
                    reader.join()
                execution["output_truncated"] = any(truncated)
                try:
                    detail = json.loads((directory / "checker.stdout").read_bytes())
                    if not isinstance(detail, dict):
                        raise ValueError("checker result is not an object")
                except ValueError:
                    detail = {"passed": False, "checks": [], "error": "checker did not return JSON"}
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                detail["passed"] = False
                detail["error"] = str(exc)
            finally:
                if server_capture is not None:
                    try:
                        state = self._state(ownership["server"])
                        early_exit = None if state["Running"] else state["ExitCode"]
                        subprocess.run(["docker", "stop", "--time", "5", ownership["server"]], capture_output=True, check=True, timeout=15)
                        stopped = self._state(ownership["server"])
                        server_capture[0].wait(timeout=15)
                        for reader in server_capture[1]:
                            reader.join()
                        stdout = (directory / "server.stdout").read_text(errors="replace")
                        stderr = (directory / "server.stderr").read_text(errors="replace")
                        logs = stdout + stderr
                        sanitizer = any(marker in logs for marker in SANITIZER_MARKERS)
                        sanitizer_categories = [name for name, markers in {
                            "address": ("AddressSanitizer",), "undefined": ("UndefinedBehaviorSanitizer", "runtime error:"),
                            "leak": ("LeakSanitizer",)}.items() if any(marker in logs for marker in markers)]
                        output_truncated = any(server_capture[2]) or execution.get("output_truncated", False)
                        detail.update(server_argv=server_argv, server_returncode=stopped["ExitCode"], early_exit=early_exit,
                                      stop_timeout=stopped["ExitCode"] == 137, sanitizer_error=sanitizer,
                                      sanitizer_categories=sanitizer_categories,
                                      server_stdout=stdout, server_stderr=stderr, output_truncated=output_truncated,
                                      project_root=str(workspace.resolve()), source_locations=_source_locations(logs, workspace), port=port)
                    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                        detail.update(passed=False, error=str(exc))
                # Always attempt both removals, even if one fails; an unclean record remains resumable.
                cleanup_errors = []
                for role in ("checker", "server"):
                    try:
                        self.executor.remove_container(ownership[role])
                    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                        cleanup_errors.append(str(exc))
                for process, readers, _ in captures:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    for reader in readers:
                        reader.join()
                ownership["cleaned"] = not cleanup_errors
                atomic_json(directory / "containers.json", ownership)
                if cleanup_errors:
                    raise RuntimeError("verification cleanup incomplete; recorded containers require recovery")
            actual = detail.get("checks", [])
            expected_ids = [c["id"] for c in acceptance["checks"]]
            complete = (isinstance(actual, list) and len(actual) == len(expected_ids) and
                        all(isinstance(r, dict) for r in actual) and [r.get("id") for r in actual] == expected_ids)
            mandatory = {c["id"] for c in acceptance["checks"] if c["required"]}
            valid = (bool(expected_ids) and bool(mandatory) and complete and
                     all(r.get("passed") is True and r.get("returncode") == 0 for r in actual if r["id"] in mandatory))
            detail["coverage_complete"] = complete
            detail["passed"] = (valid and detail.get("passed") is True and execution["returncode"] == 0 and
                                not execution["timed_out"] and detail.get("early_exit") is None and
                                detail.get("server_returncode") == 0 and detail.get("sanitizer_error") is False and
                                detail.get("output_truncated") is False and detail.get("stop_timeout") is False)
            execution["duration_ms"] = int((time.monotonic() - started) * 1000)
            rows.append({"variant": build["id"], "passed": detail["passed"] is True,
                         "execution": execution, "detail": detail})
        result = {"passed": bool(rows) and all(r["passed"] for r in rows), "variants": rows,
                  "exposure": "private_isolated", "verifier_version": VERIFIER_VERSION,
                  "randomization_version": RANDOMIZATION_VERSION}
        atomic_json(evidence_dir / "result.json", result)
        return result

    def _capture(self, identifier: str, directory: Path, prefix: str) -> tuple[subprocess.Popen[bytes], list[threading.Thread], list[bool]]:
        """Drain live container output, retaining at most the existing per-stream bound."""
        process = subprocess.Popen(["docker", "start", "--attach", identifier], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        truncated = [False, False]

        def drain(stream: Any, path: Path, index: int) -> None:
            remaining = self.executor.max_output_bytes
            with path.open("wb") as output:
                while chunk := stream.read(65536):
                    output.write(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated[index] = True
                    remaining = max(0, remaining - len(chunk))
            stream.close()

        readers = [threading.Thread(target=drain, args=(stream, directory / (prefix + suffix), index))
                   for index, (stream, suffix) in enumerate(((process.stdout, ".stdout"), (process.stderr, ".stderr")))]
        for reader in readers:
            reader.start()
        return process, readers, truncated

    @staticmethod
    def _state(identifier: str) -> dict[str, Any]:
        result = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", identifier],
                                capture_output=True, text=True, check=True, timeout=15)
        return json.loads(result.stdout)
