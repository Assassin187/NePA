"""Deterministic, study-local audit for the M1-13 natural-failure study."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nepa.config import load_config
from nepa.metrics import compute_m1_metrics


class AuditError(ValueError):
    """A controlled study-input or evidence failure."""


PARAMETERS = {
    "revision.theta2",
    "revision.theta6",
    "budgets.revision_f2_limit",
    "budgets.revision_f3_limit",
    "budgets.s6_lease_limit",
    "revision.rho_min_f2",
    "revision.rho_min_f3",
    "budgets.s6_total_attempts_cap",
    "smoke.dwell_seconds",
    "smoke.term_grace_seconds",
}
CLASSIFICATIONS = {"planning_defect", "local_implementation_defect", "indeterminate"}
DECISIONS = {"select", "retain_current", "insufficient_evidence"}
RUN_FIELDS = {
    "logical_run_id",
    "run_id",
    "group_id",
    "status",
    "exclusion_reason",
    "config_snapshot_sha256",
    "returned_model_identities",
    "interruptions",
    "evidence_refs",
    "workspace_commit",
    "workspace_tree",
}
SAMPLE_FIELDS = {
    "run_id",
    "task_uid",
    "classification",
    "rationale",
    "decisive_refs",
    "required_revision_boundary",
    "trigger_observations",
    "authority_basis",
    "evidence_refs",
}
REF_FIELDS = {"kind", "path", "sha256"}
RUN_REQUIRED_KINDS = {
    "run",
    "initial_plan",
    "active_plan",
    "terminal_state",
    "state_history",
    "revision_ledger",
    "s6_receipt",
    "build",
    "smoke",
}
SAMPLE_REQUIRED_KINDS = {
    "initial_plan",
    "active_plan",
    "terminal_state",
    "state_history",
    "revision_ledger",
    "attempt",
    "diagnostic",
    "build",
    "smoke",
}
REPORT_NAME = "04-root-cause-report.md"
RESULTS_NAME = "03-root-cause-results.json"
MANIFEST_NAME = "02-sample-manifest.json"
RECOMMENDATION_NAME = "05-parameter-recommendation.json"


def _artifact_names(protocol: Mapping[str, Any]) -> dict[str, str]:
    defaults = {
        "manifest": MANIFEST_NAME,
        "results": RESULTS_NAME,
        "report": REPORT_NAME,
        "recommendation": RECOMMENDATION_NAME,
    }
    declared = protocol.get("artifact_files")
    if declared is None:
        return defaults
    if not isinstance(declared, Mapping):
        raise AuditError("protocol artifact files must be an object")
    _closed(declared, set(defaults), "protocol artifact files")
    return {key: str(declared[key]) for key in defaults}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--runs-root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--protocol-check", action="store_true")
    return parser.parse_args(argv)


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"malformed JSON at {path}: {exc}") from exc


def _closed(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    actual = set(value)
    if actual != fields:
        raise AuditError(f"{label} fields differ: missing={sorted(fields-actual)}, extra={sorted(actual-fields)}")


def _confined(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AuditError(f"{label} path must be a non-empty relative path")
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AuditError(f"{label} path escapes its root: {relative}") from exc
    return candidate


def _validated_ref(run_root: Path, value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AuditError(f"{label} must be an evidence reference")
    _closed(value, REF_FIELDS, label)
    path = _confined(run_root, value["path"], label)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AuditError(f"{label} is missing: {value['path']}") from exc
    if _sha(data) != value["sha256"]:
        raise AuditError(f"{label} hash drift: {value['path']}")
    return {"kind": str(value["kind"]), "path": str(value["path"]), "sha256": str(value["sha256"])}


def load_protocol(study_root: Path) -> dict[str, Any]:
    candidates = sorted(study_root.glob("01*preregistration.md"))
    if not candidates:
        raise AuditError("preregistration is missing")
    path = candidates[-1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditError(f"unable to read preregistration: {exc}") from exc
    matches = re.findall(r"<!-- protocol-json\s*(\{.*?\})\s*-->", text, flags=re.DOTALL)
    if len(matches) != 1:
        raise AuditError("preregistration must contain exactly one protocol-json block")
    try:
        value = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise AuditError(f"malformed protocol JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError("protocol must be an object")
    return value


def validate_protocol(protocol: Mapping[str, Any], study_root: Path, runs_root: Path) -> None:
    fields = {
        "schema_version", "study_id", "configuration_group", "group_size", "replacement_group",
        "max_replacement_groups", "logical_run_ids", "replacement_logical_run_ids", "inputs",
        "config_path", "config_sha256", "config_snapshot_sha256", "sandbox_digest", "revision_limits",
        "environment_variable_names", "requested_routes", "pricing_reference", "sample_unit",
        "inclusion_rule", "exclusion_rule", "configuration_equivalence", "cache_policy",
        "interruption_policy", "group_invalidation_policy", "stopping_rule", "command",
    }
    if "superseded_runs" in protocol:
        fields.add("superseded_runs")
    if "artifact_files" in protocol:
        fields.add("artifact_files")
    if "requested_role_overrides" in protocol:
        fields.add("requested_role_overrides")
    _closed(protocol, fields, "protocol")
    if protocol["schema_version"] != "1.0" or protocol["group_size"] != 5:
        raise AuditError("protocol must declare schema 1.0 and N=5")
    logical = protocol["logical_run_ids"]
    replacement = protocol["replacement_logical_run_ids"]
    if not isinstance(logical, list) or len(logical) != 5 or len(set(logical)) != 5:
        raise AuditError("protocol must declare five unique primary logical run identities")
    if not isinstance(replacement, list) or len(replacement) != 5 or len(set(replacement)) != 5:
        raise AuditError("protocol must preregister five unique replacement identities")
    if set(logical) & set(replacement) or protocol["max_replacement_groups"] != 1:
        raise AuditError("replacement-group stopping rule is invalid")
    if protocol["revision_limits"] != {"f2": 0, "f3": 0}:
        raise AuditError("formal runs must freeze F2/F3 at 0/0")
    workspace = Path.cwd().resolve()
    for index, ref in enumerate(protocol["inputs"]):
        if set(ref) != {"path", "sha256"}:
            raise AuditError(f"protocol input {index} has invalid fields")
        path = _confined(workspace, ref["path"], f"protocol input {index}")
        if not path.is_file() or _sha(path.read_bytes()) != ref["sha256"]:
            raise AuditError(f"protocol input hash drift: {ref['path']}")
    config_path = _confined(workspace, protocol["config_path"], "study config")
    if _sha(config_path.read_bytes()) != protocol["config_sha256"]:
        raise AuditError("study config byte hash drift")
    config = load_config(config_path)
    if config.snapshot_sha256 != protocol["config_snapshot_sha256"]:
        raise AuditError("study config snapshot drift")
    if config.budgets.revision_f2_limit != 0 or config.budgets.revision_f3_limit != 0 or config.run.until != "s6":
        raise AuditError("study config must retain F2/F3=0/0 and until=s6")
    for tier, expected in protocol["requested_routes"].items():
        actual = config.tiers[tier].model_dump(mode="json")
        if actual != expected:
            raise AuditError(f"requested route drift: {tier}")
    for role, expected in protocol.get("requested_role_overrides", {}).items():
        actual = config.roles[role].model_dump(mode="json")
        if actual != expected:
            raise AuditError(f"requested role override drift: {role}")
    for label, name in _artifact_names(protocol).items():
        _confined(study_root, name, f"protocol {label} artifact")
    pricing = protocol["pricing_reference"]
    if pricing.get("cost_is_scientific_gate") is not False or pricing.get("provider_cached_tokens_available") is not False:
        raise AuditError("reference cost must not be a scientific gate or invent cached-token evidence")
    claude_pricing = pricing.get("claude_usd_per_million")
    if claude_pricing is not None and claude_pricing.get("provider_cached_input") != 0.5:
        raise AuditError("Claude provider cached-input reference price drift")
    superseded = protocol.get("superseded_runs", [])
    if not isinstance(superseded, list):
        raise AuditError("superseded runs must be a list")
    superseded_fields = {"logical_run_id", "group_id", "run_id", "config_snapshot_sha256", "reason"}
    for index, item in enumerate(superseded):
        if not isinstance(item, Mapping):
            raise AuditError(f"superseded run {index} must be an object")
        _closed(item, superseded_fields, f"superseded run {index}")
    superseded_logical = [item["logical_run_id"] for item in superseded]
    if len(superseded_logical) != len(set(superseded_logical)):
        raise AuditError("superseded logical run identities must be unique")
    if set(superseded_logical) & (set(logical) | set(replacement)):
        raise AuditError("superseded logical runs cannot belong to the active groups")
    if not str(protocol["sandbox_digest"]).startswith("sha256:"):
        raise AuditError("sandbox digest is not frozen")
    if study_root.resolve() == runs_root.resolve():
        raise AuditError("study and runs roots must be distinct")


def _load_run_package(run_root: Path) -> dict[str, Any]:
    def optional(relative: str) -> Any | None:
        path = run_root / relative
        return _read_json(path) if path.is_file() else None

    package: dict[str, Any] = {}
    for key, relative in (
        ("run", "run.json"),
        ("state", "plan/plan_state.json"),
        ("revision_ledger", "plan/revision_ledger.json"),
        ("s6_receipt", "plan/s6_receipt.json"),
        ("state_history", "plan/state_history.json"),
    ):
        value = optional(relative)
        if value is not None:
            package[key] = value
    active = optional("plan/active_plan.json")
    if isinstance(active, Mapping) and isinstance(active.get("path"), str):
        package["plan"] = optional(str(active["path"]))
    run = package.get("run", {})
    initial = run.get("stages", {}).get("s4", {}).get("output_refs", {}).get("plan") if isinstance(run, Mapping) else None
    if isinstance(initial, Mapping) and isinstance(initial.get("path"), str):
        package["initial_plan"] = optional(str(initial["path"]))
    return package


def _git_fact(workspace: Path, argument: str) -> str:
    result = subprocess.run(["git", "-C", str(workspace), "rev-parse", argument], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AuditError(f"unable to verify workspace Git fact {argument}")
    return result.stdout.strip()


def _validate_real_run(row: Mapping[str, Any], protocol: Mapping[str, Any], runs_root: Path) -> dict[str, Any]:
    _closed(row, RUN_FIELDS, f"run {row.get('run_id', '?')}")
    superseded = {item["logical_run_id"]: item for item in protocol.get("superseded_runs", [])}
    allowed_logical = set(protocol["logical_run_ids"]) | set(protocol["replacement_logical_run_ids"]) | set(superseded)
    if row["logical_run_id"] not in allowed_logical:
        raise AuditError("undeclared logical run identity")
    if row["logical_run_id"] in superseded:
        expected_group = superseded[row["logical_run_id"]]["group_id"]
        expected_config = superseded[row["logical_run_id"]]["config_snapshot_sha256"]
        if row["status"] != "excluded":
            raise AuditError("a superseded configuration run cannot be admitted")
    else:
        expected_group = protocol["configuration_group"] if row["logical_run_id"] in protocol["logical_run_ids"] else protocol["replacement_group"]
        expected_config = protocol["config_snapshot_sha256"]
    if row["group_id"] != expected_group:
        raise AuditError("logical run belongs to the wrong configuration group")
    if row["status"] not in {"admitted", "excluded", "pending"}:
        raise AuditError("unsupported run status")
    run_root = _confined(runs_root, row["run_id"], "run")
    package = _load_run_package(run_root)
    run = package.get("run")
    if not isinstance(run, Mapping) or run.get("run_id") != row["run_id"]:
        raise AuditError("run identity is missing or inconsistent")
    if run.get("config_snapshot_sha256") != expected_config or row["config_snapshot_sha256"] != expected_config:
        raise AuditError("requested configuration drift")
    budgets = run.get("config_snapshot", {}).get("budgets", {})
    if budgets.get("revision_f2_limit") != 0 or budgets.get("revision_f3_limit") != 0:
        raise AuditError("nonzero revision limits are inadmissible")
    termination = run.get("termination_kind")
    if row["status"] == "pending":
        if termination is not None:
            raise AuditError("a finalized run cannot be pending")
    elif termination is None:
        raise AuditError("an admitted/excluded run must be finalized")
    elif termination == "internal_error" and row["status"] != "excluded":
        raise AuditError("a finalized internal_error run cannot be admitted")
    elif termination != "internal_error" and row["status"] == "excluded" and row["exclusion_reason"] == "internal_error":
        raise AuditError("internal_error exclusion lacks a finalized internal_error")
    refs = [_validated_ref(run_root, value, f"run evidence {index}") for index, value in enumerate(row["evidence_refs"])]
    kinds = {value["kind"] for value in refs}
    if row["status"] == "admitted":
        if not RUN_REQUIRED_KINDS <= kinds:
            if run.get("stages", {}).get("s4", {}).get("status") != "failed":
                raise AuditError(f"admitted run evidence is incomplete: {sorted(RUN_REQUIRED_KINDS-kinds)}")
            early_exit_kinds = {"run", "report_json", "report_md", "llm_trace"}
            if not early_exit_kinds <= kinds:
                raise AuditError(f"admitted early-exit evidence is incomplete: {sorted(early_exit_kinds-kinds)}")
    identities = row["returned_model_identities"]
    if not isinstance(identities, list) or any(set(item) != {"value", "call_count"} for item in identities):
        raise AuditError("returned-model disclosure is malformed")
    if any((item["value"] is not None and not isinstance(item["value"], str)) or not isinstance(item["call_count"], int) or item["call_count"] < 1 for item in identities):
        raise AuditError("returned-model disclosure values are invalid")
    if not isinstance(row["interruptions"], list) or any(set(item) != {"kind", "trace_ref", "resumed"} for item in row["interruptions"]):
        raise AuditError("interruption evidence is malformed")
    workspace = run_root / "workspace"
    if row["status"] == "admitted" and workspace.is_dir():
        if _git_fact(workspace, "HEAD") != row["workspace_commit"] or _git_fact(workspace, "HEAD^{tree}") != row["workspace_tree"]:
            raise AuditError("workspace commit/tree drift")
    return package


def _validate_sample(sample: Mapping[str, Any], admitted: set[str], runs_root: Path) -> None:
    _closed(sample, SAMPLE_FIELDS, f"sample {sample.get('run_id', '?')}/{sample.get('task_uid', '?')}")
    if sample["run_id"] not in admitted:
        raise AuditError("sample references an excluded or unknown run")
    if sample["classification"] not in CLASSIFICATIONS:
        raise AuditError("unsupported classification")
    rationale = sample["rationale"]
    if not isinstance(rationale, str) or not 20 <= len(rationale) <= 2000:
        raise AuditError("classification rationale must contain 20..2000 characters")
    basis = sample["authority_basis"]
    if not isinstance(basis, list) or not basis or any(item not in {"machine_fact", "artifact_content", "analyst_judgment"} for item in basis):
        raise AuditError("classification cannot rely on exhaustion or model prose alone")
    if sample["classification"] == "planning_defect":
        if sample["required_revision_boundary"] not in {"F2", "F3"} or not sample["trigger_observations"]:
            raise AuditError("planning defect requires an F2/F3 boundary and machine trigger facts")
    elif sample["required_revision_boundary"] is not None:
        raise AuditError("only planning defects may name a required revision boundary")
    run_root = _confined(runs_root, sample["run_id"], "sample run")
    package = _load_run_package(run_root)
    tasks = package.get("state", {}).get("tasks", [])
    matches = [item for item in tasks if item.get("task_uid") == sample["task_uid"]]
    if len(matches) != 1 or matches[0].get("status") not in {"blocked", "blocked_by_dependency"}:
        raise AuditError("sample task is not uniquely terminal and unsuccessful")
    refs = [_validated_ref(run_root, value, "sample evidence") for value in sample["evidence_refs"]]
    kinds = {value["kind"] for value in refs}
    if not SAMPLE_REQUIRED_KINDS <= kinds:
        raise AuditError(f"sample evidence is incomplete: {sorted(SAMPLE_REQUIRED_KINDS-kinds)}")
    paths = {value["path"] for value in refs}
    if not isinstance(sample["decisive_refs"], list) or not sample["decisive_refs"] or not set(sample["decisive_refs"]) <= paths:
        raise AuditError("decisive references are missing from bound evidence")


def validate_manifest(
    manifest: Mapping[str, Any], protocol: Mapping[str, Any], study_root: Path, runs_root: Path
) -> list[dict[str, Any]]:
    _closed(manifest, {"schema_version", "study_id", "admitted_group_id", "real_runs", "excluded_runs", "natural_failure_samples", "synthetic_evidence"}, "manifest")
    if manifest["schema_version"] != "1.0" or manifest["study_id"] != protocol["study_id"]:
        raise AuditError("manifest identity/version drift")
    real = manifest["real_runs"]
    excluded = manifest["excluded_runs"]
    if not isinstance(real, list) or not isinstance(excluded, list):
        raise AuditError("run collections must be arrays")
    all_rows = real + excluded
    logical = [row.get("logical_run_id") for row in all_rows if isinstance(row, Mapping)]
    run_ids = [row.get("run_id") for row in all_rows if isinstance(row, Mapping)]
    if len(logical) != len(set(logical)) or len(run_ids) != len(set(run_ids)):
        raise AuditError("duplicate run identity")
    packages = [_validate_real_run(row, protocol, runs_root) for row in all_rows]
    internal_groups = {
        row["group_id"] for row, package in zip(all_rows, packages, strict=True)
        if package.get("run", {}).get("termination_kind") == "internal_error"
    }
    if any(row["status"] == "admitted" and row["group_id"] in internal_groups for row in all_rows):
        raise AuditError("selective retention after finalized internal_error is forbidden")
    admitted_group = manifest["admitted_group_id"]
    admitted_rows = [row for row in real if row.get("status") == "admitted"]
    if admitted_group is not None:
        if admitted_group not in {protocol["configuration_group"], protocol["replacement_group"]}:
            raise AuditError("undeclared admitted configuration group")
        if len(admitted_rows) != 5 or {row["group_id"] for row in admitted_rows} != {admitted_group}:
            raise AuditError("admitted group must contain exactly one complete N=5 configuration group")
    elif admitted_rows:
        raise AuditError("admitted runs require an admitted_group_id")
    admitted = {row["run_id"] for row in admitted_rows}
    samples = manifest["natural_failure_samples"]
    if not isinstance(samples, list):
        raise AuditError("natural_failure_samples must be an array")
    identities = [(item.get("run_id"), item.get("task_uid")) for item in samples if isinstance(item, Mapping)]
    if len(identities) != len(set(identities)):
        raise AuditError("duplicate natural sample identity")
    for sample in samples:
        _validate_sample(sample, admitted, runs_root)
    synthetic = manifest["synthetic_evidence"]
    if not isinstance(synthetic, list) or any(not isinstance(item, Mapping) or set(item) != {"id", "description", "refs"} for item in synthetic):
        raise AuditError("synthetic evidence table is malformed")
    if any(item.get("run_id") in admitted for item in synthetic):
        raise AuditError("synthetic evidence cannot enter the real-run set")
    return [package for row, package in zip(all_rows, packages, strict=True) if row.get("status") == "admitted"]


def validate_recommendations(record: Mapping[str, Any], results: Mapping[str, Any]) -> None:
    _closed(record, {"schema_version", "parameters", "d1_14_established", "production_enablement_established"}, "recommendation")
    rows = record["parameters"]
    if not isinstance(rows, list) or {row.get("parameter") for row in rows if isinstance(row, Mapping)} != PARAMETERS or len(rows) != len(PARAMETERS):
        raise AuditError("recommendation must contain each required parameter exactly once")
    for row in rows:
        _closed(row, {"parameter", "decision", "proposed_value", "rationale", "refs"}, f"parameter {row.get('parameter')}")
        if row["decision"] not in DECISIONS or not isinstance(row["rationale"], str) or not row["rationale"].strip() or not row["refs"]:
            raise AuditError("parameter decision, rationale, and citations are required")
        if row["decision"] == "select" and row["proposed_value"] is None:
            raise AuditError("selected parameter requires a proposed value")
        if row["decision"] != "select" and row["proposed_value"] is not None:
            raise AuditError("only a selected parameter may provide a proposed value")
        if any(not isinstance(ref, str) or not (ref.startswith("results:") or ref.startswith("sample:")) for ref in row["refs"]):
            raise AuditError("parameter citations must reference results or admitted samples")
    insufficient = results["counts"]["natural_failure_tasks"] == 0 or results["counts"]["classified_tasks"] < 2
    if insufficient and (record["d1_14_established"] or record["production_enablement_established"]):
        raise AuditError("insufficient evidence cannot establish D1.14 or production enablement")
    limits = {row["parameter"]: row for row in rows if row["parameter"] in {"budgets.revision_f2_limit", "budgets.revision_f3_limit"}}
    if insufficient and any(row["decision"] != "insufficient_evidence" for row in limits.values()):
        raise AuditError("insufficient evidence must leave F2/F3 unsupported")


def _quartiles(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"median": None, "iqr": None, "min": None, "max": None}
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]
    lower = ordered[(len(ordered) - 1) // 4]
    upper = ordered[(3 * (len(ordered) - 1)) // 4]
    return {"median": median, "iqr": upper - lower, "min": ordered[0], "max": ordered[-1]}


def compute_results(
    protocol: Mapping[str, Any], manifest: Mapping[str, Any], run_packages: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    samples = sorted(manifest["natural_failure_samples"], key=lambda item: (item["run_id"], item["task_uid"]))
    classes = Counter(item["classification"] for item in samples)
    classified = classes["planning_defect"] + classes["local_implementation_defect"]
    natural = len(samples)
    identities: Counter[str] = Counter()
    for row in manifest["real_runs"]:
        if row["status"] == "admitted":
            for item in row["returned_model_identities"]:
                identities["<absent>" if item["value"] is None else item["value"]] += item["call_count"]
    triggers = Counter(trigger for item in samples for trigger in item["trigger_observations"])
    costs = [float(package.get("run", {}).get("budget_used", {}).get("cost_usd", 0.0)) for package in run_packages]
    ordered_packages = sorted(run_packages, key=lambda item: item["run"]["run_id"])
    denominator = classified
    return {
        "schema_version": "1.0",
        "study_id": protocol["study_id"],
        "admitted_group_id": manifest["admitted_group_id"],
        "counts": {
            "admitted_runs": len(run_packages),
            "excluded_runs": len(manifest["excluded_runs"]),
            "natural_failure_tasks": natural,
            "classified_tasks": classified,
            "indeterminate_tasks": classes["indeterminate"],
            "planning_defects": classes["planning_defect"],
            "local_implementation_defects": classes["local_implementation_defect"],
            "synthetic_rows": len(manifest["synthetic_evidence"]),
        },
        "classification_coverage": classified / natural if natural else None,
        "two_category_proportions": {
            "denominator": denominator,
            "planning_defect": classes["planning_defect"] / denominator if denominator else None,
            "local_implementation_defect": classes["local_implementation_defect"] / denominator if denominator else None,
        },
        "run_ids": [package["run"]["run_id"] for package in ordered_packages],
        "run_outcomes": [
            {
                "run_id": package["run"]["run_id"],
                "outcome": package["run"].get("outcome"),
                "termination_kind": package["run"].get("termination_kind"),
                "s4_status": package["run"].get("stages", {}).get("s4", {}).get("status"),
                "s4_error": package["run"].get("stages", {}).get("s4", {}).get("error"),
            }
            for package in ordered_packages
        ],
        "sample_ids": [f"{item['run_id']}:{item['task_uid']}" for item in samples],
        "returned_model_identity_call_counts": dict(sorted(identities.items())),
        "trigger_observations": dict(sorted(triggers.items())),
        "per_configuration": {str(manifest["admitted_group_id"]): {"run_count": len(run_packages), "cost_usd": _quartiles(costs)}},
        "telemetry_observed": {
            "cost_usd": sum(costs),
            "tokens_in": sum(int(package.get("run", {}).get("budget_used", {}).get("tokens_in", 0)) for package in run_packages),
            "tokens_out": sum(int(package.get("run", {}).get("budget_used", {}).get("tokens_out", 0)) for package in run_packages),
        },
        "m1_metrics": {
            package["run"]["run_id"]: compute_m1_metrics(package)
            for package in ordered_packages
        },
        "real_samples": [
            {"run_id": item["run_id"], "task_uid": item["task_uid"], "classification": item["classification"]}
            for item in samples
        ],
        "synthetic_evidence": sorted(manifest["synthetic_evidence"], key=lambda item: item["id"]),
        "pricing_reference": protocol["pricing_reference"],
        "limitations": [
            "N=5 is descriptive; no p-values or significance claims are made.",
            "Task samples within a Run are correlated.",
            "Provider cached-input cost is not recomputed without cached-token evidence.",
        ],
    }


def render_report(results: Mapping[str, Any], manifest: Mapping[str, Any], recommendations: Mapping[str, Any]) -> str:
    counts = results["counts"]
    lines = [
        "# M1-13 natural-failure root-cause report", "", "Generated deterministically from the frozen protocol, manifest, and referenced Run artifacts.", "",
        "## Sample and classification summary", "",
        "| measure | value |", "| --- | ---: |",
    ]
    for key in ("admitted_runs", "excluded_runs", "natural_failure_tasks", "classified_tasks", "indeterminate_tasks", "planning_defects", "local_implementation_defects"):
        lines.append(f"| {key} | {counts[key]} |")
    lines += ["", "## Real natural-failure samples", "", "| run_id | task_uid | classification |", "| --- | --- | --- |"]
    for row in results["real_samples"]:
        lines.append(f"| {row['run_id']} | {row['task_uid']} | {row['classification']} |")
    if not results["real_samples"]:
        lines.append("| — | — | no natural failure samples |")
    lines += ["", "## Admitted run outcomes", "", "| run_id | termination | S4 status | S4 error |", "| --- | --- | --- | --- |"]
    for row in results["run_outcomes"]:
        lines.append(f"| {row['run_id']} | {row['termination_kind']} | {row['s4_status']} | {row['s4_error']} |")
    lines += ["", "## Separate synthetic mechanism evidence", "", "| id | description |", "| --- | --- |"]
    for row in results["synthetic_evidence"]:
        lines.append(f"| {row['id']} | {row['description']} |")
    if not results["synthetic_evidence"]:
        lines.append("| — | no synthetic rows declared |")
    lines += ["", "## Returned model identity disclosure", "", "| observed identity | call count |", "| --- | ---: |"]
    for identity, count in results["returned_model_identity_call_counts"].items():
        lines.append(f"| {identity} | {count} |")
    if not results["returned_model_identity_call_counts"]:
        lines.append("| — | 0 |")
    lines += ["", "## Parameter and production conclusion", ""]
    lines.append(f"D1.14 established: `{str(recommendations['d1_14_established']).lower()}`.")
    lines.append(f"Production enablement established: `{str(recommendations['production_enablement_established']).lower()}`.")
    lines += ["", "## Cost reference and limitations", ""]
    telemetry = results["telemetry_observed"]
    lines.append(
        f"Admitted-run telemetry: {telemetry['tokens_in']} input tokens, {telemetry['tokens_out']} output tokens, "
        f"and ${telemetry['cost_usd']:.6f} recorded USD cost."
    )
    lines.append("Costs are operational references only and do not affect admission, classification, parameter selection, or acceptance. USD telemetry uses the frozen runtime prices; CNY source prices and the fixed ¥7.2/USD accounting conversion remain disclosed in the machine result. Claude cached-input is $0.5/1M but is not applied without trustworthy cached-token counts; NePA local cache hits remain zero incremental cost.")
    lines.append("")
    for limitation in results["limitations"]:
        lines.append(f"- {limitation}")
    return "\n".join(lines) + "\n"


def _validate_owner_decision(study_root: Path, protocol: Mapping[str, Any] | None = None) -> None:
    text = (study_root / "06-owner-decision.md").read_text(encoding="utf-8")
    matches = re.findall(r"<!-- owner-decision-json\s*(\{.*?\})\s*-->", text, flags=re.DOTALL)
    if not matches:
        if "Status: pending responsible-owner action." not in text:
            raise AuditError("owner decision is neither pending nor machine-verifiable")
        return
    if len(matches) != 1:
        raise AuditError("owner decision must contain at most one decision block")
    value = json.loads(matches[0])
    _closed(value, {"status", "date", "reviewed_hashes", "limitations", "requested_corrections"}, "owner decision")
    if value["status"] != "approved" or not value["date"]:
        raise AuditError("owner decision block is not an explicit dated approval")
    preregistrations = sorted(study_root.glob("01*preregistration.md"))
    if not preregistrations:
        raise AuditError("owner decision cannot resolve the active preregistration")
    required = {preregistrations[-1].name, *_artifact_names(protocol or {}).values()}
    if set(value["reviewed_hashes"]) != required:
        raise AuditError("owner decision does not name the complete reviewed package")
    for name, digest in value["reviewed_hashes"].items():
        if _sha((study_root / name).read_bytes()) != digest:
            raise AuditError("owner decision reviewed hash drift")


def run(study_root: Path, runs_root: Path, *, check: bool, protocol_check: bool) -> None:
    study_root = study_root.resolve()
    runs_root = runs_root.resolve()
    protocol = load_protocol(study_root)
    validate_protocol(protocol, study_root, runs_root)
    if protocol_check:
        return
    names = _artifact_names(protocol)
    manifest = _read_json(study_root / names["manifest"])
    recommendation = _read_json(study_root / names["recommendation"])
    if not isinstance(manifest, Mapping) or not isinstance(recommendation, Mapping):
        raise AuditError("manifest and recommendation must be objects")
    packages = validate_manifest(manifest, protocol, study_root, runs_root)
    results = compute_results(protocol, manifest, packages)
    validate_recommendations(recommendation, results)
    report = render_report(results, manifest, recommendation)
    _validate_owner_decision(study_root, protocol)
    outputs = {names["results"]: _canonical(results), names["report"]: report.encode()}
    if check:
        for name, expected in outputs.items():
            try:
                actual = (study_root / name).read_bytes()
            except OSError as exc:
                raise AuditError(f"generated output is missing: {name}") from exc
            if actual != expected:
                raise AuditError(f"generated output drift: {name}")
        return
    for name, data in outputs.items():
        (study_root / name).write_bytes(data)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        run(args.study_root, args.runs_root, check=args.check, protocol_check=args.protocol_check)
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"M1-13 audit failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
