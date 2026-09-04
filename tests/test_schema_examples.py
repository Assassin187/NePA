import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from nepa.speclib.lint import _schema_errors, lint_test_summary


SCHEMA_DIR = Path(__file__).parents[1] / "nepa" / "schemas"
EXAMPLE_DIR = SCHEMA_DIR / "examples"


def test_schema_examples():
    schema_paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
    assert len(schema_paths) == 63

    for schema_path in schema_paths:
        example_name = schema_path.name.removesuffix(".schema.json") + ".example.json"
        example_path = EXAMPLE_DIR / example_name
        assert example_path.is_file(), f"missing example for {schema_path.name}"

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        example = json.loads(example_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _example(name: str) -> dict:
    return json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8"))


def _run_variant(**changes):
    value = _example("run.example.json")
    value.update(changes)
    return value


def test_schema_contract_audit():
    expected = {
        "specs-requirements.schema.json",
        "segments.schema.json",
        "run.schema.json",
        "spec-review.schema.json",
        "merge-decisions.schema.json",
        "test-summary.schema.json",
        "repair-log.schema.json",
        "report.schema.json",
        "test-bundle.schema.json",
        "target-profile.schema.json",
        "architecture-draft.schema.json",
        "architecture-patch.schema.json",
        "architecture-patch-application.schema.json",
        "architecture-validation.schema.json",
        "calibration-lineage.schema.json",
        "calibration-batch.schema.json",
        "trial-request-ref.schema.json",
        "trial-response-ref.schema.json",
        "trial-validation.schema.json",
        "calibration-report.schema.json",
        "calibration-development-protocol.schema.json",
        "calibration-prompt-version.schema.json",
        "calibration-prompt-snapshot.schema.json",
        "calibration-prompt-revision.schema.json",
        "calibration-attempt-declaration.schema.json",
        "calibration-attempt-outcome.schema.json",
        "calibration-development-extension.schema.json",
        "calibration-development-assessment.schema.json",
        "calibration-development-outcome.schema.json",
        "calibration-development-selection.schema.json",
        "calibration-development-handoff.schema.json",
        "calibration-development-protocol-bundle.schema.json",
        "calibration-prompt-version-bundle.schema.json",
        "calibration-prompt-snapshot-bundle.schema.json",
        "calibration-prompt-revision-bundle.schema.json",
        "calibration-attempt-declaration-bundle.schema.json",
        "calibration-development-selection-bundle.schema.json",
        "calibration-development-handoff-bundle.schema.json",
        "calibration-recovery-authorization.schema.json",
        "calibration-recovery-provenance.schema.json",
        "calibration-recovery-predecessor-attestation.schema.json",
        "calibration-recovery-protocol.schema.json",
        "calibration-recovery-prompt-snapshot.schema.json",
        "calibration-recovery-revision.schema.json",
        "calibration-recovery-attempt-declaration.schema.json",
        "calibration-recovery-attempt-outcome.schema.json",
        "calibration-recovery-repair-diff.schema.json",
        "calibration-recovery-report.schema.json",
        "calibration-recovery-assessment.schema.json",
        "calibration-recovery-quality-audit.schema.json",
        "calibration-recovery-terminal.schema.json",
        "calibration-recovery-handoff.schema.json",
        "calibration-baseline-protocol.schema.json",
        "calibration-baseline-version.schema.json",
        "calibration-baseline-snapshot.schema.json",
        "calibration-baseline-revision.schema.json",
        "calibration-baseline-attempt-declaration.schema.json",
        "calibration-baseline-attempt-outcome.schema.json",
        "calibration-baseline-assessment.schema.json",
        "calibration-baseline-outcome.schema.json",
        "calibration-baseline-selection.schema.json",
        "calibration-baseline-handoff.schema.json",
        "calibration-baseline-owner-approval.schema.json",
    }
    assert {path.name for path in SCHEMA_DIR.glob("*.schema.json")} == expected

    run = _schema("run.schema.json")
    assert run["properties"]["created_at"]["pattern"].endswith("Z$")
    assert any(
        condition.get("if", {}).get("properties", {}).get("termination_kind", {}).get("const") == "planned_stop"
        and condition.get("then", {}).get("properties", {}).get("exit_code", {}).get("const") == 0
        for condition in run["allOf"]
    )
    assert any(
        condition.get("if", {}).get("properties", {}).get("termination_kind", {}).get("const") == "internal_error"
        and condition.get("then", {}).get("properties", {}).get("exit_code", {}).get("const") == 1
        for condition in run["allOf"]
    )
    assert _schema("segments.schema.json")["properties"]["coverage_ratio"]["maximum"] == 1
    assert _schema("spec-review.schema.json")["allOf"]
    repair = _schema("repair-log.schema.json")
    assert any("regression_summary_ref" in condition.get("then", {}).get("required", []) for condition in repair["allOf"])


def test_schema_negative_run_terminal_conditions():
    schema = _schema("run.schema.json")
    validator = Draft202012Validator(schema)

    planned_stop_nonzero = _run_variant(termination_kind="planned_stop", exit_code=7)
    planned_stop_outcome = _run_variant(termination_kind="planned_stop", exit_code=0, outcome="success")
    completed_failed = _run_variant(termination_kind="completed", exit_code=0, outcome="failed")
    completed_request = _run_variant(
        termination_kind="completed",
        exit_code=0,
        outcome="success",
        termination_request={
            "kind": "controlled_exit",
            "stage": "s1",
            "requested_at": "2026-08-11T12:34:56Z",
            "reason": {"code": "STOPPED", "detail": "stop"},
        },
    )
    internal_error_outcome = _run_variant(termination_kind="internal_error", exit_code=1, outcome="failed")
    nonterminal_outcome = _run_variant(outcome="success")

    for value in [
        planned_stop_nonzero,
        planned_stop_outcome,
        completed_failed,
        completed_request,
        internal_error_outcome,
        nonterminal_outcome,
    ]:
        assert not validator.is_valid(value)


def test_schema_positive_run_terminal_conditions():
    validator = Draft202012Validator(_schema("run.schema.json"))
    completed = _run_variant(termination_kind="completed", exit_code=0, outcome="success")
    planned_stop = _run_variant(termination_kind="planned_stop", exit_code=0)
    controlled_exit = _run_variant(
        termination_kind="controlled_exit",
        exit_code=10,
        outcome="degraded",
        termination_request={
            "kind": "controlled_exit",
            "stage": "s1",
            "requested_at": "2026-08-11T12:34:56Z",
            "reason": {"code": "BUDGET_EXHAUSTED", "detail": "budget"},
        },
    )
    controlled_exit_failed = copy.deepcopy(controlled_exit)
    controlled_exit_failed["stages"]["s1"]["status"] = "failed"
    controlled_exit["stages"]["s1"]["status"] = "pending"

    assert validator.is_valid(completed)
    assert validator.is_valid(planned_stop)
    assert validator.is_valid(controlled_exit)
    assert validator.is_valid(controlled_exit_failed)


def test_schema_negative_controlled_exit_requires_failed_or_pending_stage():
    validator = Draft202012Validator(_schema("run.schema.json"))
    invalid = _run_variant(
        termination_kind="controlled_exit",
        exit_code=10,
        outcome="degraded",
        termination_request={
            "kind": "controlled_exit",
            "stage": "s1",
            "requested_at": "2026-08-11T12:34:56Z",
            "reason": {"code": "BUDGET_EXHAUSTED", "detail": "budget"},
        },
    )
    invalid["stages"]["s1"]["status"] = "done"

    assert not validator.is_valid(invalid)


def test_runtime_schema_initial_spec_run_skips_pre_runtime_stages():
    validator = Draft202012Validator(_schema("run.schema.json"))
    value = _example("run.example.json")

    assert value["stages"]["s1"]["status"] == "skipped"
    assert value["stages"]["s2"]["status"] == "skipped"
    assert value["stages"]["s3"]["status"] == "skipped"
    assert all(value["stages"][stage]["status"] == "pending" for stage in ("s4", "s5", "s6", "s7", "s8", "s9"))
    assert validator.is_valid(value)


def test_runtime_schema_rejects_unbound_output_reference():
    validator = Draft202012Validator(_schema("run.schema.json"))
    value = _example("run.example.json")
    value["stages"]["s4"]["output_refs"] = {"plan": {"path": "plan/plan.json", "sha256": "not-a-sha"}}

    assert not validator.is_valid(value)


def test_report_availability_requires_reason_for_missing_value():
    validator = Draft202012Validator(_schema("report.schema.json"))
    value = _example("report.example.json")
    value["req_coverage"] = {"status": "unavailable", "value": None}

    assert not validator.is_valid(value)


def test_schema_negative_run_created_at_must_be_utc_iso8601():
    validator = Draft202012Validator(_schema("run.schema.json"), format_checker=FormatChecker())
    invalid = _run_variant(created_at="not-a-time")
    invalid_date = _run_variant(created_at="2026-99-99T99:99:99Z")
    invalid_calendar = _run_variant(created_at="2026-02-29T12:34:56Z")
    utc = _run_variant(created_at="2026-08-11T12:34:56Z")
    offset = _run_variant(created_at="2026-08-11T12:34:56+00:00")

    assert not validator.is_valid(invalid)
    assert not validator.is_valid(invalid_date)
    assert any(error["path"] == "/created_at" for error in _schema_errors(invalid_calendar, "run.schema.json"))
    assert validator.is_valid(utc)
    assert not validator.is_valid(offset)


def test_schema_negative_coverage_ratio_must_be_a_proportion():
    validator = Draft202012Validator(_schema("segments.schema.json"))
    invalid = _example("segments.example.json")
    invalid["coverage_ratio"] = 2
    lower_boundary = _example("segments.example.json")
    lower_boundary["coverage_ratio"] = 0
    upper_boundary = _example("segments.example.json")
    upper_boundary["coverage_ratio"] = 1

    assert not validator.is_valid(invalid)
    assert validator.is_valid(lower_boundary)
    assert validator.is_valid(upper_boundary)


def test_schema_negative_spec_review_passed_requires_no_blocker():
    validator = Draft202012Validator(_schema("spec-review.schema.json"))
    invalid = _example("spec-review.example.json")
    invalid["issues"] = [{
        "severity": "blocker",
        "element": "/requirements/0",
        "description": "Missing source evidence.",
        "suggestion": "Add a source reference.",
    }]
    invalid["passed"] = True

    assert not validator.is_valid(invalid)


def test_schema_negative_accepted_repair_requires_regression_evidence():
    validator = Draft202012Validator(_schema("repair-log.schema.json"))
    invalid = _example("repair-log.example.json")
    invalid["status"] = "accepted"
    invalid["commit_sha"] = "0" * 40
    invalid["repair_evidence_ref"] = {
        "path": "repair/evidence/repair-001.json",
        "sha256": "0" * 64,
    }

    assert not validator.is_valid(invalid)


def test_schema_negative_duplicate_test_summary_objects_are_rejected():
    validator = Draft202012Validator(_schema("test-summary.schema.json"))
    invalid = _example("test-summary.example.json")
    invalid["build_results"].append(copy.deepcopy(invalid["build_results"][0]))

    assert not validator.is_valid(invalid)


def test_test_summary_semantic_validation_accepts_valid_round_and_items():
    first = _example("test-summary.example.json")
    later = copy.deepcopy(first)
    later["round_id"] = "round-002"
    later["parent_round_id"] = "round-001"

    assert lint_test_summary(first)["valid"]
    assert lint_test_summary(later)["valid"]


def test_test_summary_semantic_validation_rejects_duplicate_variant_id():
    invalid = _example("test-summary.example.json")
    invalid["build_results"].append({
        "variant_id": "san",
        "result": "error",
        "warnings": 0,
        "errors": 1,
        "failure_excerpt": "compiler failed",
    })

    report = lint_test_summary(invalid)

    assert not report["valid"]
    assert any(error["code"] == "TEST_SUMMARY_VARIANT_DUPLICATE" for error in report["errors"])


def test_test_summary_semantic_validation_rejects_duplicate_nodeid():
    invalid = _example("test-summary.example.json")
    invalid["cases"] = [
        {"nodeid": "tests/l1_codec/test.py::test_one", "req_ids": ["REQ-001"], "result": "pass", "duration_ms": 1},
        {
            "nodeid": "tests/l1_codec/test.py::test_one",
            "req_ids": ["REQ-001"],
            "result": "error",
            "duration_ms": 2,
            "failure_excerpt": "error",
        },
    ]

    report = lint_test_summary(invalid)

    assert not report["valid"]
    assert any(error["code"] == "TEST_SUMMARY_NODEID_DUPLICATE" for error in report["errors"])


def test_test_summary_semantic_validation_rejects_invalid_parent_round_order():
    invalid = _example("test-summary.example.json")
    invalid["round_id"] = "round-002"
    invalid["parent_round_id"] = "round-002"

    report = lint_test_summary(invalid)

    assert not report["valid"]
    assert any(error["code"] == "TEST_SUMMARY_PARENT_ROUND_INVALID" for error in report["errors"])
