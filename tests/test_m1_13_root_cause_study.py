import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "experiments/m1-13-natural-failure-root-cause-study/scripts/audit_root_causes.py"
SPEC = importlib.util.spec_from_file_location("m1_13_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _write(path: Path, value) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    return {"path": path.name, "sha256": hashlib.sha256(data).hexdigest()}


def _artifact(run_root: Path, relative: str, value, kind: str) -> dict[str, str]:
    path = run_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    return {"kind": kind, "path": relative, "sha256": hashlib.sha256(data).hexdigest()}


def _protocol() -> dict:
    return {
        "study_id": "study",
        "configuration_group": "g01",
        "replacement_group": "g02",
        "logical_run_ids": [f"g01-r0{i}" for i in range(1, 6)],
        "replacement_logical_run_ids": [f"g02-r0{i}" for i in range(1, 6)],
        "config_snapshot_sha256": "c" * 64,
        "pricing_reference": {"cost_is_scientific_gate": False},
    }


def _run_fixture(runs: Path, index: int, *, status: str = "done", termination: str = "planned_stop", blocked: bool = False, group: str = "g01") -> dict:
    run_id = f"actual-{group}-{index}"
    root = runs / run_id
    task_status = "blocked" if blocked else status
    state = {"tasks": [{"id": "T-001", "task_uid": "a" * 16, "status": task_status}]}
    plan = {"tasks": [{"id": "T-001", "task_uid": "a" * 16}]}
    initial = _artifact(root, "plan/versions/plan.json", plan, "initial_plan")
    active = _artifact(root, "plan/active_plan.json", {"path": "plan/versions/plan.json", "sha256": initial["sha256"]}, "active_plan")
    terminal = _artifact(root, "plan/plan_state.json", state, "terminal_state")
    history = _artifact(root, "plan/state_history.json", [{"tasks": state["tasks"]}], "state_history")
    ledger = _artifact(root, "plan/revision_ledger.json", {"entries": []}, "revision_ledger")
    receipt = _artifact(root, "plan/s6_receipt.json", {"s6_build_ok": not blocked, "smoke_pass": not blocked, "artifacts_checked": 1}, "s6_receipt")
    build = _artifact(root, "build/result.json", {"status": "failed" if blocked else "passed"}, "build")
    smoke = _artifact(root, "smoke/result.json", {"status": "failed" if blocked else "passed"}, "smoke")
    attempt = _artifact(root, "attempts/a/attempt_001.json", {"task_uid": "a" * 16, "attempt": 1}, "attempt")
    diagnostic = _artifact(root, "attempts/a/diagnostic.json", {"facts": ["compiler failure"]}, "diagnostic")
    run = {
        "run_id": run_id,
        "termination_kind": termination,
        "config_snapshot_sha256": "c" * 64,
        "config_snapshot": {"budgets": {"revision_f2_limit": 0, "revision_f3_limit": 0}},
        "budget_used": {"cost_usd": float(index)},
        "stages": {"s4": {"output_refs": {"plan": {"path": initial["path"], "sha256": initial["sha256"]}}}},
    }
    run_ref = _artifact(root, "run.json", run, "run")
    refs = [run_ref, initial, active, terminal, history, ledger, receipt, build, smoke]
    row = {
        "logical_run_id": f"{group}-r0{index}", "run_id": run_id, "group_id": group,
        "status": "excluded" if termination == "internal_error" else "admitted", "exclusion_reason": "internal_error" if termination == "internal_error" else None,
        "config_snapshot_sha256": "c" * 64,
        "returned_model_identities": [{"value": None if index == 1 else f"alias-{index % 2}", "call_count": 1}],
        "interruptions": [{"kind": "timeout", "trace_ref": "trace/llm_calls.ndjson", "resumed": True}] if index == 2 else [],
        "evidence_refs": refs, "workspace_commit": "fixture", "workspace_tree": "fixture",
    }
    sample_refs = [initial, active, terminal, history, ledger, attempt, diagnostic, build, smoke]
    sample = {
        "run_id": run_id, "task_uid": "a" * 16, "classification": "local_implementation_defect",
        "rationale": "The accepted plan remained valid and the compiler evidence identifies a local source defect.",
        "decisive_refs": [diagnostic["path"]], "required_revision_boundary": None,
        "trigger_observations": [], "authority_basis": ["machine_fact", "artifact_content"], "evidence_refs": sample_refs,
    }
    return {"row": row, "sample": sample}


def _early_exit_fixture(runs: Path, index: int) -> dict:
    run_id = f"actual-g01-{index}"
    root = runs / run_id
    run = {
        "run_id": run_id,
        "termination_kind": "controlled_exit",
        "config_snapshot_sha256": "c" * 64,
        "config_snapshot": {"budgets": {"revision_f2_limit": 0, "revision_f3_limit": 0}},
        "budget_used": {"cost_usd": float(index)},
        "stages": {"s4": {"status": "failed"}},
    }
    refs = [
        _artifact(root, "run.json", run, "run"),
        _artifact(root, "report/report.json", {"outcome": "failed"}, "report_json"),
        _artifact(root, "report/report.md", {"summary": "S4 failed"}, "report_md"),
        _artifact(root, "trace/llm_calls.ndjson", {"validation": "fail"}, "llm_trace"),
    ]
    return {
        "row": {
            "logical_run_id": f"g01-r0{index}", "run_id": run_id, "group_id": "g01",
            "status": "admitted", "exclusion_reason": None, "config_snapshot_sha256": "c" * 64,
            "returned_model_identities": [{"value": "deepseek-v4-pro", "call_count": 1}],
            "interruptions": [], "evidence_refs": refs, "workspace_commit": None, "workspace_tree": None,
        }
    }


def _manifest(rows, samples=None, *, group="g01", synthetic=None):
    return {
        "schema_version": "1.0", "study_id": "study", "admitted_group_id": group,
        "real_runs": [item["row"] for item in rows], "excluded_runs": [],
        "natural_failure_samples": samples or [], "synthetic_evidence": synthetic or [],
    }


def _recommendations():
    return {
        "schema_version": "1.0", "d1_14_established": False, "production_enablement_established": False,
        "parameters": [
            {"parameter": name, "decision": "insufficient_evidence", "proposed_value": None,
             "rationale": "The admitted real evidence does not support changing this value.", "refs": ["results:natural_failure_task_count"]}
            for name in sorted(audit.PARAMETERS)
        ],
    }


def test_arguments_are_required_and_modes_are_exclusive():
    with pytest.raises(SystemExit):
        audit.parse_args([])
    with pytest.raises(SystemExit):
        audit.parse_args(["--study-root", "s", "--runs-root", "r", "--check", "--protocol-check"])


def test_confinement_rejects_escape_and_malformed_json(tmp_path):
    with pytest.raises(audit.AuditError, match="escapes"):
        audit._confined(tmp_path, "../escape", "fixture")
    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="malformed"):
        audit._read_json(malformed)


def test_run_admission_accepts_absent_aliased_provider_identity_and_interruption(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    packages = audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)
    assert len(packages) == 5


def test_provider_identity_mismatch_alone_never_rejects(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    rows[4]["row"]["returned_model_identities"] = [{"value": "unexpected-alias", "call_count": 4}]
    assert len(audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)) == 5


def test_run_admission_accepts_complete_early_controlled_exit_group(tmp_path):
    runs = tmp_path / "runs"
    rows = [_early_exit_fixture(runs, i) for i in range(1, 6)]
    packages = audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)
    assert len(packages) == 5
    assert all("plan" not in package and "state" not in package for package in packages)


def test_run_admission_rejects_requested_config_drift_and_nonzero_limits(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    rows[0]["row"]["config_snapshot_sha256"] = "d" * 64
    with pytest.raises(audit.AuditError, match="configuration drift"):
        audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)


def test_run_admission_rejects_undeclared_or_mixed_groups(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    rows[0]["row"]["logical_run_id"] = "calibration-or-injected"
    with pytest.raises(audit.AuditError, match="undeclared"):
        audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    rows[0]["row"]["group_id"] = "g02"
    with pytest.raises(audit.AuditError, match="wrong configuration group"):
        audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    path = runs / rows[0]["row"]["run_id"] / "run.json"
    value = json.loads(path.read_text())
    value["config_snapshot"]["budgets"]["revision_f2_limit"] = 1
    path.write_text(json.dumps(value), encoding="utf-8")
    rows[0]["row"]["evidence_refs"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(audit.AuditError, match="nonzero"):
        audit.validate_manifest(_manifest(rows), _protocol(), tmp_path, runs)


def test_group_invalidation_rejects_selective_retention(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    invalid = _run_fixture(runs, 1, termination="internal_error")
    manifest = _manifest(rows)
    manifest["excluded_runs"] = [invalid["row"]]
    with pytest.raises(audit.AuditError, match="duplicate run identity|selective retention"):
        audit.validate_manifest(manifest, _protocol(), tmp_path, runs)


def test_sample_identity_and_evidence_binding_accept_repeated_attempt_history(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i, blocked=i == 1) for i in range(1, 6)]
    extra = _artifact(runs / rows[0]["row"]["run_id"], "attempts/a/attempt_002.json", {"attempt": 2}, "attempt")
    rows[0]["sample"]["evidence_refs"].append(extra)
    assert len(audit.validate_manifest(_manifest(rows, [rows[0]["sample"]]), _protocol(), tmp_path, runs)) == 5


def test_sample_identity_rejects_duplicate_cross_run_and_hash_drift(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i, blocked=i in {1, 2}) for i in range(1, 6)]
    with pytest.raises(audit.AuditError, match="duplicate natural"):
        audit.validate_manifest(_manifest(rows, [rows[0]["sample"], copy.deepcopy(rows[0]["sample"])]), _protocol(), tmp_path, runs)
    broken = copy.deepcopy(rows[0]["sample"])
    broken["evidence_refs"][0]["sha256"] = "0" * 64
    with pytest.raises(audit.AuditError, match="hash drift"):
        audit.validate_manifest(_manifest(rows, [broken]), _protocol(), tmp_path, runs)


@pytest.mark.parametrize("classification", ["planning_defect", "local_implementation_defect", "indeterminate"])
def test_classification_accepts_all_three_categories(tmp_path, classification):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i, blocked=i == 1) for i in range(1, 6)]
    sample = rows[0]["sample"]
    sample["classification"] = classification
    if classification == "planning_defect":
        sample["required_revision_boundary"] = "F2"
        sample["trigger_observations"] = ["TR-2"]
        sample["rationale"] = "Machine dependency facts show that the accepted task boundary requires an F2 split."
    assert len(audit.validate_manifest(_manifest(rows, [sample]), _protocol(), tmp_path, runs)) == 5


def test_classification_rejects_missing_rationale_and_model_only_assertion(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i, blocked=i == 1) for i in range(1, 6)]
    sample = rows[0]["sample"]
    sample["rationale"] = "short"
    with pytest.raises(audit.AuditError, match="rationale"):
        audit.validate_manifest(_manifest(rows, [sample]), _protocol(), tmp_path, runs)


def test_classification_rejects_forced_category_without_planning_boundary(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i, blocked=i == 1) for i in range(1, 6)]
    sample = rows[0]["sample"]
    sample["classification"] = "planning_defect"
    with pytest.raises(audit.AuditError, match="F2/F3 boundary"):
        audit.validate_manifest(_manifest(rows, [sample]), _protocol(), tmp_path, runs)
    sample["rationale"] = "The Diagnoser alone asserted that this was a planning defect."
    sample["authority_basis"] = ["diagnoser_prose"]
    with pytest.raises(audit.AuditError, match="exhaustion or model prose"):
        audit.validate_manifest(_manifest(rows, [sample]), _protocol(), tmp_path, runs)


def test_aggregate_denominator_and_synthetic_separation(tmp_path):
    runs = tmp_path / "runs"
    rows = [_run_fixture(runs, i, blocked=i <= 3) for i in range(1, 6)]
    samples = [rows[i]["sample"] for i in range(3)]
    samples[0]["classification"] = "planning_defect"
    samples[0]["required_revision_boundary"] = "F3"
    samples[0]["trigger_observations"] = ["TR-7"]
    samples[1]["classification"] = "local_implementation_defect"
    samples[2]["classification"] = "indeterminate"
    manifest = _manifest(rows, samples, synthetic=[{"id": "fixture", "description": "mechanism only", "refs": ["tests:test"]}])
    packages = audit.validate_manifest(manifest, _protocol(), tmp_path, runs)
    result = audit.compute_results(_protocol(), manifest, packages)
    assert result["counts"]["natural_failure_tasks"] == 3
    assert result["counts"]["classified_tasks"] == 2
    assert result["two_category_proportions"]["planning_defect"] == 0.5
    assert result["counts"]["synthetic_rows"] == 1


def test_recommendation_requires_complete_parameters_and_non_enabling_insufficiency():
    results = {"counts": {"natural_failure_tasks": 0, "classified_tasks": 0}}
    record = _recommendations()
    audit.validate_recommendations(record, results)
    record["parameters"].pop()
    with pytest.raises(audit.AuditError, match="each required parameter"):
        audit.validate_recommendations(record, results)


def test_recommendation_rejects_unsupported_decision_and_uncited_value():
    results = {"counts": {"natural_failure_tasks": 3, "classified_tasks": 3}}
    record = _recommendations()
    record["parameters"][0]["decision"] = "guess"
    with pytest.raises(audit.AuditError, match="decision"):
        audit.validate_recommendations(record, results)
    record = _recommendations()
    record["parameters"][0].update({"decision": "select", "proposed_value": 1, "refs": []})
    with pytest.raises(audit.AuditError, match="citations"):
        audit.validate_recommendations(record, results)


def test_fabricated_owner_decision_is_rejected(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    decision = {
        "status": "approved", "date": "2026-09-11", "limitations": [], "requested_corrections": [],
        "reviewed_hashes": {name: "0" * 64 for name in (
            "01-preregistration.md", "02-sample-manifest.json", audit.RESULTS_NAME,
            audit.REPORT_NAME, "05-parameter-recommendation.json",
        )},
    }
    (study / "06-owner-decision.md").write_text(
        "<!-- owner-decision-json\n" + json.dumps(decision) + "\n-->\n", encoding="utf-8"
    )
    for name in decision["reviewed_hashes"]:
        (study / name).write_text("not reviewed", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="reviewed hash drift"):
        audit._validate_owner_decision(study)


def test_deterministic_generation_check_mode_and_drift(tmp_path, monkeypatch):
    study = tmp_path / "study"
    runs = tmp_path / "runs"
    study.mkdir()
    rows = [_run_fixture(runs, i) for i in range(1, 6)]
    protocol = _protocol()
    prereg = "<!-- protocol-json\n" + json.dumps(protocol) + "\n-->\n"
    (study / "01-preregistration.md").write_text(prereg, encoding="utf-8")
    (study / "02-sample-manifest.json").write_text(json.dumps(_manifest(rows)), encoding="utf-8")
    (study / "05-parameter-recommendation.json").write_text(json.dumps(_recommendations()), encoding="utf-8")
    (study / "06-owner-decision.md").write_text("Status: pending responsible-owner action.\n", encoding="utf-8")
    monkeypatch.setattr(audit, "validate_protocol", lambda *args: None)
    audit.run(study, runs, check=False, protocol_check=False)
    first = {(study / name).read_bytes() for name in (audit.RESULTS_NAME, audit.REPORT_NAME)}
    audit.run(study, runs, check=True, protocol_check=False)
    audit.run(study, runs, check=False, protocol_check=False)
    second = {(study / name).read_bytes() for name in (audit.RESULTS_NAME, audit.REPORT_NAME)}
    assert first == second
    (study / audit.REPORT_NAME).write_text("drift", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="drift"):
        audit.run(study, runs, check=True, protocol_check=False)


def test_cached_input_price_is_reference_only_and_not_a_scientific_gate():
    protocol = _protocol()
    protocol["pricing_reference"] = {
        "cost_is_scientific_gate": False,
        "provider_cached_tokens_available": False,
        "claude_usd_per_million": {"provider_cached_input": 0.5},
    }
    result = audit.compute_results(protocol, {"admitted_group_id": None, "natural_failure_samples": [], "excluded_runs": [], "real_runs": [], "synthetic_evidence": []}, [])
    assert result["pricing_reference"]["claude_usd_per_million"]["provider_cached_input"] == 0.5
    assert result["counts"]["natural_failure_tasks"] == 0
