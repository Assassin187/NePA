"""Host-owned independent verification; protocol knowledge stays in input assets."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .sandbox import SandboxExecutor


class VerificationRunner:
    def __init__(self, executor: SandboxExecutor):
        self.executor = executor

    def run(self, target: dict[str, Any], acceptance: dict[str, Any], workspace: Path,
            checks_root: Path, evidence_dir: Path) -> dict[str, Any]:
        evidence_dir.mkdir(parents=True, exist_ok=False)
        rows = []
        for build in target["builds"]:
            payload = {"run": target["run"], "artifact": "/workspace/" + build["artifact"], "checks": acceptance["checks"]}
            path = evidence_dir / (build["id"] + ".json")
            path.write_text(json.dumps(payload))
            result = self.executor.exec(
                ["python", "/nepa_tools/verification_worker.py", "/verification/" + path.name],
                str(workspace), sum(c["timeout_s"] for c in acceptance["checks"]) + 15,
                readonly={"/checks": checks_root, "/verification": evidence_dir, "/nepa_tools": Path(__file__).parent},
            )
            try:
                detail = json.loads(result.stdout)
            except ValueError:
                detail = {"passed": False, "error": "supervisor did not return JSON"}
            rows.append({"variant": build["id"], "passed": result.returncode == 0 and detail.get("passed") is True,
                         "execution": asdict(result), "detail": detail})
        return {"passed": all(r["passed"] for r in rows), "variants": rows}
