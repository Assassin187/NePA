"""One serial generation path through actual coding, verification and export."""
from __future__ import annotations
import os
import shutil
import subprocess
from typing import Any
import uuid

from .agents.session import CodingSession
from .report import publish_report
from .run_store import BudgetExhausted, RunStore, RunStoreError, tree_hashes


class Orchestrator:
    def __init__(self, session: CodingSession):
        self.session = session

    def _delivery(self, store: RunStore, target: dict[str, Any], acceptance: dict[str, Any]) -> bool:
        if store.run.get("delivery"):
            delivery = store.run["delivery"]
            if tree_hashes(store.root / delivery["path"]) != delivery["files"]:
                raise RunStoreError("published delivery changed; refusing to overwrite")
            return store.run["final_checks"]["result"]["passed"] is True
        if (store.root / "delivery").exists():
            orphan = store.root / "export-attempts" / ("unpublished-" + uuid.uuid4().hex)
            orphan.parent.mkdir(exist_ok=True)
            os.replace(store.root / "delivery", orphan)
        candidate = store.root / "export-attempts" / uuid.uuid4().hex
        shutil.copytree(store.project, candidate, symlinks=True)
        if not (candidate / "README.md").is_file() or not any(candidate.rglob("*.c")):
            result: dict[str, Any] = {"passed": False, "error": "export requires README.md and C sources"}
        else:
            builds = self.session.builder.run(target, candidate, clean=True)
            verification = None
            if builds["passed"]:
                verification = self.session.verifier.run(
                    target, acceptance, candidate, store.root / "inputs/checks",
                    store.root / "evidence" / ("export-verification-" + uuid.uuid4().hex))
            result = {"passed": builds["passed"] and verification is not None and verification["passed"],
                      "build": builds, "verification": verification}
        ref = store.evidence("exports/" + candidate.name + ".json", result)
        store.run["final_checks"] = {"result": result, "evidence": ref}
        store.save()
        if not result["passed"]:
            return False
        destination = store.root / "delivery"
        if destination.exists():
            raise RunStoreError("refusing to replace an existing delivery")
        os.replace(candidate, destination)
        store.run["delivery"] = {"path": "delivery", "files": tree_hashes(destination),
                                 "checkpoint": store.run["accepted_checkpoint"]}
        store.save()
        return True

    def run(self, store: RunStore, *, resume: bool = False) -> int:
        with store.lock(), store.deadline():
            try:
                if store.run["status"] == "success":
                    publish_report(store)
                    return 0
                if resume:
                    store.recover()
                spec, target, acceptance = store.inputs()
                store.run["status"] = "running"
                store.save()
                image = subprocess.run(["docker", "image", "inspect", store.config.sandbox.image, "--format", "{{.Id}}"],
                                       capture_output=True, text=True)
                if image.returncode:
                    raise RuntimeError("configured sandbox image is unavailable; build it before generation")
                if store.run.get("sandbox_image") and store.run["sandbox_image"] != image.stdout.strip():
                    raise RunStoreError("sandbox image changed; start a new run")
                store.run["sandbox_image"] = image.stdout.strip()
                store.save()
                while True:
                    store.check_budget()
                    plan = store.plan()
                    pending = [t for t in plan["tasks"] if store.run["tasks"][t["id"]]["status"] != "passed"]
                    if not pending:
                        break
                    if not self.session.run(pending[0]):
                        raise RuntimeError("task sessions exhausted: " + pending[0]["id"])
                while True:
                    all_passed = all(task['status'] == 'passed' for task in store.run['tasks'].values())
                    if all_passed and self._delivery(store, target, acceptance):
                        break
                    if store.run["final_repairs"] >= store.config.budgets.final_repairs:
                        raise RuntimeError("final independent checks failed after bounded repairs")
                    store.run["final_repairs"] += 1
                    store.save()
                    final = store.plan()["tasks"][-1]
                    if not self.session.run(final, repair=True, feedback=store.run["final_checks"]):
                        continue
                store.run.update({"status": "success", "exit_code": 0, "reason": "all tasks, mandatory checks and export passed"})
            except BudgetExhausted as exc:
                store.run.update({"status": "budget_exhausted", "exit_code": 3, "reason": str(exc)})
            except KeyboardInterrupt:
                store.run.update({"status": "interrupted", "exit_code": 130, "reason": "interrupted; incomplete attempt preserved"})
            except RunStoreError as exc:
                store.run.update({"status": "invalid", "exit_code": 20, "reason": str(exc)})
            except RuntimeError as exc:
                store.run.update({"status": "failed", "exit_code": 2, "reason": str(exc)})
            except Exception as exc:
                store.run.update({"status": "internal_error", "exit_code": 1, "reason": f"{type(exc).__name__}: {exc}"})
            try:
                publish_report(store)
            except Exception as exc:
                store.run.update({"status": "internal_error", "exit_code": 1, "reason": f"report publication failed: {exc}"})
            store.save()
            return int(store.run["exit_code"])

    def resume(self, store: RunStore) -> int:
        return self.run(store, resume=True)
