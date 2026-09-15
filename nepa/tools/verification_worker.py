"""Stdlib-only trusted supervisor, executed inside the verification sandbox."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

SANITIZER_MARKERS = ("AddressSanitizer", "UndefinedBehaviorSanitizer", "runtime error:", "LeakSanitizer")


def supervise(payload: dict) -> dict:
    supervision_started = time.monotonic()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    params = {"host": "127.0.0.1", "port": str(port), "artifact": payload["artifact"]}
    argv = [part.format(**params) for part in payload["run"]]
    rows = []
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        server = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        early_exit = None
        try:
            for check in payload["checks"]:
                command = [part.format(**params) for part in check["argv"]]
                check_started = time.monotonic()
                try:
                    result = subprocess.run(command, capture_output=True, timeout=check["timeout_s"])
                    stdout = result.stdout.decode(errors="replace")
                    stderr = result.stderr.decode(errors="replace")
                    rows.append({"id": check["id"], "required": check["required"], "req_ids": check["req_ids"],
                                 "argv": command, "returncode": result.returncode, "stdout": stdout, "stderr": stderr,
                                 "passed": result.returncode == 0,
                                 "elapsed_s": time.monotonic() - check_started})
                except subprocess.TimeoutExpired as exc:
                    rows.append({"id": check["id"], "required": check["required"], "req_ids": check["req_ids"],
                                 "argv": command, "returncode": None, "passed": False, "error": "client timeout",
                                 "stdout": (exc.stdout or b"").decode(errors="replace"),
                                 "stderr": (exc.stderr or b"").decode(errors="replace"),
                                 "elapsed_s": time.monotonic() - check_started})
            early_exit = server.poll()
        finally:
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode(errors="replace")
            stderr = err.read().decode(errors="replace")
        diagnostics = stdout + stderr + "".join(r.get("stdout", "") + r.get("stderr", "") for r in rows)
        sanitizer = any(marker in diagnostics for marker in SANITIZER_MARKERS)
        passed = (early_exit is None and server.returncode == 0 and not timed_out and not sanitizer
                  and all(r["passed"] for r in rows if r["required"]))
        return {"passed": passed, "checks": rows, "server_argv": argv, "server_returncode": server.returncode,
                "early_exit": early_exit, "stop_timeout": timed_out, "sanitizer_error": sanitizer,
                "server_stdout": stdout, "server_stderr": stderr, "host": params["host"], "port": port,
                "elapsed_s": time.monotonic() - supervision_started}


if __name__ == "__main__":
    try:
        value = supervise(json.loads(Path(sys.argv[1]).read_bytes()))
    except Exception as exc:
        value = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(value))
    raise SystemExit(0 if value["passed"] else 1)
