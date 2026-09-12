"""Truthful deterministic delivery reports; no model-judged success."""
from __future__ import annotations
from typing import Any
from .run_store import RunStore, atomic_json, tree_hashes


def requirement_verification(req_id: str, target: dict[str, Any], acceptance: dict[str, Any],
                             final: dict[str, Any] | None) -> dict[str, Any]:
    """Join only the latest export check; a scenario pass is never whole-clause proof."""
    checks = [c for c in acceptance["checks"] if req_id in c["req_ids"]]
    final = final or {}
    verification = final.get("result", {}).get("verification") or {}
    variants = {v["variant"]: v for v in verification.get("variants", [])}
    rows = []
    for check in checks:
        for build in target["builds"]:
            variant = variants.get(build["id"], {})
            detail = variant.get("detail", {})
            execution = variant.get("execution", {})
            actual = next((c for c in detail.get("checks", []) if c["id"] == check["id"]), None)
            status = "incomplete"
            supervisor_completed = (execution.get("timed_out") is False and
                                    execution.get("returncode") == (0 if detail.get("passed") is True else 1))
            if actual is not None and final.get("evidence") and supervisor_completed:
                if actual.get("returncode") is not None:
                    clean_server = (detail.get("server_returncode") == 0 and detail.get("early_exit") is None
                                    and detail.get("stop_timeout") is False and detail.get("sanitizer_error") is False)
                    status = "passed" if actual.get("passed") is True and actual["returncode"] == 0 and clean_server else "failed"
            rows.append({"check_id": check["id"], "variant": build["id"], "required": check["required"],
                         "status": status, "returncode": actual.get("returncode") if actual else None,
                         "evidence": final.get("evidence")})
    mandatory = [row for row in rows if row["required"]]
    status = ("unverified" if not mandatory else "failed" if any(r["status"] == "failed" for r in mandatory)
              else "incomplete" if any(r["status"] != "passed" for r in mandatory) else "scenarios_passed")
    reasons = {"unverified": "No mandatory independent scenario is configured for this requirement.",
               "incomplete": "Current final-export evidence is missing or an execution did not complete.",
               "failed": "A mapped scenario or its server supervision failed on the final export.",
               "scenarios_passed": "Only the explicitly mapped scenarios passed in both build variants; not full semantic proof."}
    return {"status": status, "reason": reasons[status], "scenarios": rows}


def publish_report(store: RunStore) -> dict[str, Any]:
    input_error = None
    try:
        spec, target, acceptance = store.inputs()
        plan = store.plan()
    except (ValueError, OSError) as exc:
        if store.run["status"] == "success":
            raise
        input_error = str(exc)
        spec, plan = {"requirements": []}, {"primary_tasks": {}}
        target, acceptance = {"builds": []}, {"checks": []}
    final = store.run.get("final_checks")
    if final and final.get("evidence"):
        if store.read_ref(final["evidence"]) != final["result"]:
            raise ValueError("final export evidence does not match run state")
    claims = {c["id"]: {**c, "checkpoint": t.get("checkpoint")}
              for t in store.run["tasks"].values() for c in t["claims"]}
    requirements = []
    for requirement in spec["requirements"]:
        requirements.append({"id": requirement["id"], "primary_task": plan["primary_tasks"][requirement["id"]],
                             "agent_claim": claims.get(requirement["id"]),
                             "verification": requirement_verification(requirement["id"], target, acceptance, final),
                             "verification_scope": "only explicitly recorded independent scenarios; not full semantic proof"})
    delivery = store.run.get("delivery")
    report = {"schema_version": "4.0", "run_id": store.run_id, "status": store.run["status"],
              "exit_code": store.run["exit_code"], "reason": store.run.get("reason"),
              "inputs": store.run["inputs"], "input_verification_error": input_error,
              "config_sha256": store.run["config_sha256"],
              "runtime": store.run.get("runtime"), "sandbox_image": store.run.get("sandbox_image"),
              "plan": store.run["active_plan"], "tasks": store.run["tasks"], "requirements": requirements,
              "budget": store.run["budget"], "unknown_calls": store.run["pending_calls"], "cache_hits": 0,
              "configuration_changes": [entry for entry in store.run["history"] if entry["kind"] == "configuration_change"],
              "final_checks": store.run.get("final_checks"), "delivery": delivery,
              "limitations": ["Only the configured mandatory scenarios were independently checked.",
                              "Agent implementation claims are not proof of every requirement.",
                              "CNY estimates use configured domestic rates, request-start Asia/Shanghai periods and provider cache usage when available; missing cache usage assumes misses. Unknown calls retain peak-price reservations. Historic USD campaigns are excluded from these newly authorized CNY limits and remain unchanged. Not a provider invoice.",
                              "No claim of full protocol conformance or untested language/protocol support."],
              "reproduction": {"build": "make clean && make release san", "inputs": "inputs/",
                               "calls": "evidence/calls/", "tools": "evidence/actions/"}}
    if delivery:
        actual = tree_hashes(store.root / delivery["path"])
        if actual != delivery["files"]:
            raise ValueError("delivery changed before report publication")
    atomic_json(store.root / "report.json", report)
    return report
