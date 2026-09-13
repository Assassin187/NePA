"""Stdlib-only private checker; generated code never enters this container."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SANITIZER_MARKERS = ("AddressSanitizer", "UndefinedBehaviorSanitizer", "runtime error:", "LeakSanitizer")


def supervise(payload: dict) -> dict:
    params = {"host": "127.0.0.1", "port": str(payload["port"])}
    rows = []
    for index, check in enumerate(payload["checks"]):
        command = [part.format(**params) for part in check["argv"]]
        env = dict(os.environ)
        env["NEPA_ORACLE_SEED"] = hashlib.sha256(
            (str(payload["seed"]) + ":case:" + check["id"]).encode()).hexdigest()
        env["NEPA_ORACLE_TRACE_FILE"] = f"/verification/trace-{index:04d}.jsonl"
        row = {"id": check["id"], "required": check["required"], "req_ids": check["req_ids"],
               "argv": command, "passed": False, "category": "checker_result_invalid", "observation": {}}
        try:
            result = subprocess.run(command, capture_output=True, timeout=check["timeout_s"], env=env)
            row.update(returncode=result.returncode, stdout=result.stdout.decode(errors="replace"),
                       stderr=result.stderr.decode(errors="replace"))
            try:
                # Exactly one structured result. A zero exit status alone cannot pass.
                lines = row["stdout"].strip().splitlines()
                value = json.loads(lines[0]) if len(lines) == 1 else None
                valid = (isinstance(value, dict) and type(value.get("passed")) is bool
                         and isinstance(value.get("category"), str) and 0 < len(value["category"]) <= 80
                         and isinstance(value.get("observation"), dict))
                if valid and isinstance(value, dict):
                    row.update(category=value["category"], observation=value["observation"],
                               passed=value["passed"] is True and result.returncode == 0)
            except (ValueError, IndexError):
                pass
        except subprocess.TimeoutExpired as exc:
            row.update(returncode=None, category="checker_timeout", error="client timeout",
                       stdout=(exc.stdout or b"").decode(errors="replace"),
                       stderr=(exc.stderr or b"").decode(errors="replace"))
        rows.append(row)
    return {"passed": bool(rows) and all(r["passed"] for r in rows if r["required"]), "checks": rows}


if __name__ == "__main__":
    try:
        value = supervise(json.loads(Path(sys.argv[1]).read_bytes()))
    except Exception as exc:
        value = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(value))
    raise SystemExit(0 if value["passed"] else 1)
