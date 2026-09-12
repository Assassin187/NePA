"""Truthful deterministic delivery reports; no model-judged success."""
from __future__ import annotations
from typing import Any
from .run_store import RunStore, atomic_json, tree_hashes


def publish_report(store: RunStore) -> dict[str, Any]:
    input_error = None
    try:
        spec, _, _ = store.inputs()
        plan = store.plan()
    except (ValueError, OSError) as exc:
        if store.run["status"] == "success":
            raise
        input_error = str(exc)
        spec, plan = {"requirements": []}, {"primary_tasks": {}}
    claims = {c["id"]: {**c, "checkpoint": t.get("checkpoint")}
              for t in store.run["tasks"].values() for c in t["claims"]}
    requirements = []
    for requirement in spec["requirements"]:
        requirements.append({"id": requirement["id"], "primary_task": plan["primary_tasks"][requirement["id"]],
                             "agent_claim": claims.get(requirement["id"]),
                             "verification_scope": "only explicitly recorded independent scenarios; not full semantic proof"})
    delivery = store.run.get("delivery")
    report = {"schema_version": "3.0", "run_id": store.run_id, "status": store.run["status"],
              "exit_code": store.run["exit_code"], "reason": store.run.get("reason"),
              "inputs": store.run["inputs"], "input_verification_error": input_error,
              "config_sha256": store.run["config_sha256"],
              "runtime": store.run.get("runtime"), "sandbox_image": store.run.get("sandbox_image"),
              "plan": store.run["active_plan"], "tasks": store.run["tasks"], "requirements": requirements,
              "budget": store.run["budget"], "unknown_calls": store.run["pending_calls"], "cache_hits": 0,
              "final_checks": store.run.get("final_checks"), "delivery": delivery,
              "limitations": ["Only the configured mandatory scenarios were independently checked.",
                              "Agent implementation claims are not proof of every requirement.",
                              "No claim of full protocol conformance or untested language/protocol support."],
              "reproduction": {"build": "make clean && make release san", "inputs": "inputs/",
                               "calls": "evidence/calls/", "tools": "evidence/actions/"}}
    if delivery:
        actual = tree_hashes(store.root / delivery["path"])
        if actual != delivery["files"]:
            raise ValueError("delivery changed before report publication")
    atomic_json(store.root / "report.json", report)
    return report
