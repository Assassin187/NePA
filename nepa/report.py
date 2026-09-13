"""Truthful deterministic delivery reports; no model-judged success."""
from __future__ import annotations
from typing import Any
from .run_store import RunStore, atomic_json, tree_hashes
from .speclib.plan import digest
from .tools.verification import safe_feedback


def requirement_verification(req_id: str, target: dict[str, Any], acceptance: dict[str, Any],
                             final: dict[str, Any] | None) -> dict[str, Any]:
    """Join only the latest export check; a scenario pass is never whole-clause proof."""
    checks = [c for c in acceptance["checks"] if req_id in c["req_ids"]]
    final = final or {}
    verification = final.get("result", {}).get("verification") or {}
    variant_rows = verification.get("variants", [])
    variants = {v["variant"]: v for v in variant_rows}
    unique_variants = len(variants) == len(variant_rows)
    rows = []
    for check in checks:
        for build in target["builds"]:
            variant = variants.get(build["id"], {})
            detail = variant.get("detail", {})
            execution = variant.get("execution", {})
            actual = next((c for c in detail.get("checks", []) if c["id"] == check["id"]), None)
            status = "incomplete"
            check_rows = detail.get("checks", [])
            complete = (unique_variants and len(check_rows) == len(acceptance["checks"]) and
                        {c.get("id") for c in check_rows} == {c["id"] for c in acceptance["checks"]})
            supervisor_completed = (complete and execution.get("timed_out") is False and
                                    execution.get("returncode") in (0, 1))
            if actual is not None and final.get("evidence") and supervisor_completed:
                if actual.get("returncode") is not None:
                    clean_server = (detail.get("server_returncode") == 0 and detail.get("early_exit") is None
                                    and detail.get("stop_timeout") is False and detail.get("sanitizer_error") is False
                                    and not detail.get("output_truncated", False))
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
    report = {"schema_version": "5.0", "run_id": store.run_id, "status": store.run["status"],
              "exit_code": store.run["exit_code"], "reason": store.run.get("reason"),
              "inputs": store.run["inputs"], "private_inputs": store.run["private_inputs"],
              "exposure": "private_isolated", "verification_policy": store.run["verification_policy"],
              "private_suite_sha256": digest(store.run["private_inputs"]),
              "verifier_sha256": digest({name: sha for name, sha in store.run["runtime"]["files"].items()
                                          if name in {"tools/verification.py", "tools/verification_worker.py", "tools/sandbox.py"}}),
              "visibility": "host_only",
              "public_development": {"kind": "build_and_fixture", "build": safe_feedback((final or {}).get("result", {}).get("build") or {})},
              "private_final": safe_feedback((final or {}).get("result", {}).get("verification") or {}),
              "input_verification_error": input_error,
              "config_sha256": store.run["config_sha256"],
              "runtime": store.run.get("runtime"), "sandbox_image": store.run.get("sandbox_image"),
              "plan": store.run["active_plan"], "tasks": store.run["tasks"], "requirements": requirements,
              "budget": store.run["budget"], "phase_cost_cny": store.run["phase_cost_cny"],
              "liability_overflow_cny": store.run["liability_overflow_cny"], "unknown_calls": store.run["pending_calls"], "cache_hits": 0,
              "configuration_changes": [entry for entry in store.run["history"] if entry["kind"] == "configuration_change"],
              "final_checks": store.run.get("final_checks"), "delivery": delivery,
              "limitations": ["Only the configured mandatory scenarios were independently checked.",
                              "Agent implementation claims are not proof of every requirement.",
                              "CNY estimates use configured provider tiers and schedules: DeepSeek uses request-start Asia/Shanghai peak/off-peak periods; Qwen uses flat all-day prices within the applicable input tier. Provider cache usage is used when available; missing cache detail assumes misses. Unknown calls retain worst-case reservations. Historical campaigns remain separate. Estimates are not provider invoices.",
                              "No claim of full protocol conformance or untested language/protocol support."],
              "reproduction": {"build": "make clean && make release san", "inputs": "inputs/",
                               "calls": "evidence/calls/", "tools": "evidence/actions/"}}
    if delivery:
        actual = tree_hashes(store.root / delivery["path"])
        if actual != delivery["files"]:
            raise ValueError("delivery changed before report publication")
    atomic_json(store.root / "report.json", report)
    atomic_json(store.root / "report.public.json", public_report(report))
    return report


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Distributable Report5: explicit projection, never a scrubbed host report."""
    result = {key: report[key] for key in (
        "schema_version", "run_id", "status", "exit_code", "config_sha256", "sandbox_image",
        "budget", "phase_cost_cny", "liability_overflow_cny", "exposure", "verification_policy",
        "private_suite_sha256", "verifier_sha256", "limitations")}
    result["visibility"] = "public"
    result["inputs"] = {key: {"sha256": ref["sha256"]} for key, ref in report["inputs"].items()}
    result["runtime_sha256"] = (report.get("runtime") or {}).get("package_sha256")
    result["public_development"] = safe_feedback(report["public_development"])
    result["private_final"] = safe_feedback(report["private_final"])
    result["tasks"] = {key: {field: task[field] for field in ("status", "sessions", "decisions", "claims")}
                       for key, task in report["tasks"].items()}
    result["requirements"] = []
    for requirement in report["requirements"]:
        verification = requirement["verification"]
        result["requirements"].append({**requirement, "verification": {
            "status": verification["status"], "reason": verification["reason"],
            "scenarios": [{key: row[key] for key in ("check_id", "variant", "required", "status", "returncode")}
                          for row in verification["scenarios"]]}})
    result["delivery"] = ({"files": report["delivery"]["files"], "checkpoint": report["delivery"]["checkpoint"]}
                          if report.get("delivery") else None)
    return result
