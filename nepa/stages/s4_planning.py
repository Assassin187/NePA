"""Production S4 planning controller and initial Plan publication.

The controller is deliberately a small orchestration layer.  Semantic
preparation, architecture validation, linking, Blueprint compilation, and
Plan lint remain owned by their existing deterministic libraries.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..agents.base import AgentInvoker
from ..calibration.s4_architecture import (
    ArchitecturePlannerContractBinding,
    apply_architecture_patch_with_projection,
    map_architecture_failures_to_paths,
)
from ..config import config_snapshot_sha256
from ..llm.client import StructuredOutputError
from ..orchestrator import ControlledStageFailure, StageContext, StageResult
from ..run_store import RunStore, RunStoreError
from ..schemas import (
    flat_plan_draft_contract,
    load_schema,
    plan_critic_contract,
    task_shard_contract,
)
from ..speclib.architecture import ArchitectureError, load_architecture_draft, validate_architecture
from ..speclib.delivery import DeliveryConstraintError, compile_delivery_blueprint, compile_delivery_constraints
from ..speclib.lint import canonical_json_bytes
from ..speclib.plan import PlanError, link_plan, plan_lint, normalize_plan_draft
from ..speclib.planning import (
    PlanningContextError,
    PlanningInputError,
    PreparedArchitectureInputs,
    architecture_planner_context_preflight,
    build_planning_index,
    build_test_manifest_metadata,
    prepare_architecture_inputs,
)


LINEAGE_ID = "ee5a23a8fcbaa5dc273f36c0365707fac5a9684f050463fc32ec7fd6bc3b67a5"
DEFAULT_HANDOFF_ROOT = Path("runs/_calibration/s4-architecture") / LINEAGE_ID
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = {"task_uid", "obligation_digest", "guidance_digest", "status", "attempts", "notes", "execution_state", "revision_seq", "epoch"}


class S4Error(RuntimeError):
    """Base class for errors raised by the production S4 boundary."""

    def __init__(self, message: str, *, code: str = "S4_INVALID") -> None:
        self.code = code
        super().__init__(message)


class S4ControlledError(S4Error):
    """An expected planning or publication failure."""


class S4ArtifactDamage(S4Error):
    """Existing immutable S4 evidence cannot be reconciled."""


@dataclass(frozen=True)
class ApprovedArchitecturePromptBundle:
    lineage_root: Path
    handoff_ref: dict[str, str]
    selection_ref: dict[str, str]
    bundle_ref: dict[str, str]
    initial_ref: dict[str, str]
    repair_ref: dict[str, str]
    initial_bytes: bytes
    repair_bytes: bytes


@dataclass(frozen=True)
class CandidateCompletion:
    plan_draft_ir: dict[str, Any]
    plan: dict[str, Any]
    blueprint: dict[str, Any]
    link_report: dict[str, Any]
    lint_report: dict[str, Any]
    constraints: dict[str, Any]
    manifest: dict[str, Any]
    spec: dict[str, Any]
    config_snapshot: dict[str, Any]
    input_refs: dict[str, dict[str, str]]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _schema_errors(value: Any, schema_name: str) -> list[str]:
    errors = list(Draft202012Validator(load_schema(schema_name)).iter_errors(value))
    return [item.message for item in sorted(errors, key=lambda item: (tuple(item.absolute_path), item.message))]


def _require_schema(value: Any, schema_name: str, label: str) -> None:
    errors = _schema_errors(value, schema_name)
    if errors:
        raise S4ControlledError(f"{label} failed {schema_name}: {'; '.join(errors)}", code="S4_STRUCTURED_OUTPUT_INVALID")


def _safe_ref(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("path"), str) or not isinstance(value.get("sha256"), str):
        raise S4ArtifactDamage(f"{label} is not an artifact reference")
    path = value["path"]
    if not path or path.startswith("/") or "\\" in path or ".." in Path(path).parts or "\x00" in path:
        raise S4ArtifactDamage(f"{label} has an unsafe path")
    if _SHA256.fullmatch(value["sha256"]) is None:
        raise S4ArtifactDamage(f"{label} has an invalid SHA-256")
    return {"path": path, "sha256": value["sha256"]}


def _read_ref(root: Path, value: Mapping[str, Any], label: str) -> bytes:
    ref = _safe_ref(value, label)
    candidate = (root / ref["path"]).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
        data = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise S4ArtifactDamage(f"missing {label}: {ref['path']}") from exc
    if _sha(data) != ref["sha256"]:
        raise S4ArtifactDamage(f"{label} hash does not match its recorded reference")
    return data


def _json_ref(root: Path, value: Mapping[str, Any], label: str, schema_name: str | None = None) -> dict[str, Any]:
    data = _read_ref(root, value, label)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S4ArtifactDamage(f"{label} is not valid JSON") from exc
    if schema_name is not None:
        _require_schema(parsed, schema_name, label)
    if not isinstance(parsed, dict):
        raise S4ArtifactDamage(f"{label} must be a JSON object")
    return parsed


def _packaged_prompt_bytes(packaged_prompts: Mapping[str, Any] | None) -> dict[str, bytes]:
    paths = {"initial": "architecture_planner_initial.md", "repair": "architecture_planner_repair.md"}
    result: dict[str, bytes] = {}
    for phase, name in paths.items():
        supplied = packaged_prompts.get(phase) if packaged_prompts is not None else None
        if supplied is None:
            result[phase] = resources.files("nepa.agents.prompts").joinpath(name).read_bytes()
        elif isinstance(supplied, bytes):
            result[phase] = bytes(supplied)
        else:
            try:
                result[phase] = Path(supplied).read_bytes()
            except (OSError, TypeError) as exc:
                raise S4ArtifactDamage(f"unable to read packaged {phase} prompt") from exc
    return result


def verify_m1_4a2_handoff(
    lineage_root: str | Path,
    packaged_prompts: Mapping[str, Any] | None = None,
) -> ApprovedArchitecturePromptBundle:
    """Admit only the owner-approved, byte-bound M1-4a2 prompt pair."""

    root = Path(lineage_root).resolve()
    if root.name != LINEAGE_ID or not root.is_dir():
        raise S4ControlledError("M1-4a2 lineage root is missing or has the wrong identity", code="S4_HANDOFF_INVALID")
    handoff_path = root / "prompt-development/handoff.json"
    try:
        handoff_data = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S4ControlledError("M1-4a2 handoff is missing or unreadable", code="S4_HANDOFF_INVALID") from exc
    _require_schema(handoff_data, "calibration-baseline-handoff.schema.json", "M1-4a2 handoff")
    if handoff_data.get("lineage_id") != LINEAGE_ID or handoff_data.get("consumer") != "m1-4c":
        raise S4ControlledError("M1-4a2 handoff has the wrong lineage or consumer", code="S4_HANDOFF_INVALID")
    if handoff_data.get("selected_version") != "v1" or handoff_data.get("satisfies") != {
        "baseline_2_of_3": True,
        "protocol_neutrality": True,
        "owner_signature": True,
        "production_quality_proven": False,
    }:
        raise S4ControlledError("M1-4a2 handoff does not contain the required approval assertions", code="S4_HANDOFF_NOT_APPROVED")
    handoff_ref = {"path": "prompt-development/handoff.json", "sha256": _sha(handoff_path.read_bytes())}

    selection_ref = _safe_ref(handoff_data["selection_ref"], "handoff selection_ref")
    selection = _json_ref(root, selection_ref, "selection", "calibration-baseline-selection.schema.json")
    if selection.get("status") != "selected" or selection.get("selected_version") != "v1":
        raise S4ControlledError("M1-4a2 selection is not the selected V1 record", code="S4_HANDOFF_INVALID")
    if selection.get("lineage_id") != LINEAGE_ID or selection.get("bundle_ref") != handoff_data["bundle_ref"] or selection.get("assessment_ref") != handoff_data["assessment_ref"]:
        raise S4ControlledError("M1-4a2 selection does not match the handoff", code="S4_HANDOFF_DRIFT")
    if selection_ref["path"] != "prompt-development/selection.json":
        raise S4ControlledError("M1-4a2 selection path is not lineage-relative", code="S4_HANDOFF_INVALID")

    approval_ref = _safe_ref(handoff_data["owner_approval_ref"], "owner approval reference")
    approval = _json_ref(root, approval_ref, "owner approval", "calibration-baseline-owner-approval.schema.json")
    if approval.get("approved") is not True:
        raise S4ControlledError("M1-4a2 owner approval is not affirmative", code="S4_HANDOFF_NOT_APPROVED")

    assessment = _json_ref(root, handoff_data["assessment_ref"], "assessment", "calibration-baseline-assessment.schema.json")
    if assessment.get("lineage_id") != LINEAGE_ID or assessment.get("version") != "v1" or assessment.get("screening_pass") is not True:
        raise S4ControlledError("M1-4a2 assessment is not a passing V1 assessment", code="S4_HANDOFF_NOT_APPROVED")

    bundle_ref = _safe_ref(handoff_data["bundle_ref"], "prompt bundle reference")
    bundle = _json_ref(root, bundle_ref, "prompt bundle", "calibration-baseline-snapshot.schema.json")
    if bundle.get("lineage_id") != LINEAGE_ID or bundle.get("version") != "v1" or bundle.get("byte_encoding") != "utf-8-raw-template":
        raise S4ControlledError("M1-4a2 prompt bundle identity is invalid", code="S4_HANDOFF_INVALID")
    initial_ref = _safe_ref(bundle["initial_ref"], "initial prompt reference")
    repair_ref = _safe_ref(bundle["repair_ref"], "repair prompt reference")
    initial_bytes = _read_ref(root, initial_ref, "initial prompt")
    repair_bytes = _read_ref(root, repair_ref, "repair prompt")
    if _sha(initial_bytes) != initial_ref["sha256"] or _sha(repair_bytes) != repair_ref["sha256"]:
        raise S4ControlledError("M1-4a2 prompt hash evidence is inconsistent", code="S4_HANDOFF_DRIFT")
    packaged = _packaged_prompt_bytes(packaged_prompts)
    if packaged["initial"] != initial_bytes or packaged["repair"] != repair_bytes:
        raise S4ControlledError("packaged ArchitecturePlanner prompt bytes differ from the approved bundle", code="S4_HANDOFF_DRIFT")
    return ApprovedArchitecturePromptBundle(
        lineage_root=root,
        handoff_ref=handoff_ref,
        selection_ref=selection_ref,
        bundle_ref=bundle_ref,
        initial_ref=initial_ref,
        repair_ref=repair_ref,
        initial_bytes=initial_bytes,
        repair_bytes=repair_bytes,
    )


class TaskPlannerContractBinding:
    """Bind TaskPlanner to exactly one local state-free shard contract."""

    def __init__(self, invoker: AgentInvoker) -> None:
        self.invoker = invoker
        self.schema, self.example = task_shard_contract()

    def invoke(self, *, inputs: Mapping[str, Any], run_id: str, task_id: str, attempt: int = 1) -> Any:
        return self.invoker.invoke(
            role="task_planner", inputs=inputs, output_schema=self.schema, output_example=self.example,
            run_id=run_id, stage="S4", task_id=task_id, attempt=attempt, use_cache=False,
        )


class PlanCriticContractBinding:
    """Bind PlanCritic to the closed verdict/issue contract."""

    def __init__(self, invoker: AgentInvoker) -> None:
        self.invoker = invoker
        self.schema, self.example = plan_critic_contract()

    def invoke(self, *, inputs: Mapping[str, Any], run_id: str, task_id: str, attempt: int = 1) -> Any:
        return self.invoker.invoke(
            role="plan_critic", inputs=inputs, output_schema=self.schema, output_example=self.example,
            run_id=run_id, stage="S4", task_id=task_id, attempt=attempt, use_cache=False,
        )


class FlatPlanBaselineContractBinding:
    """Bind the explicit flat comparison arm to its semantic draft contract."""

    def __init__(self, invoker: AgentInvoker) -> None:
        self.invoker = invoker
        self.schema, self.example = flat_plan_draft_contract()

    def invoke(self, *, inputs: Mapping[str, Any], run_id: str, attempt: int = 1) -> Any:
        return self.invoker.invoke(
            role="flat_plan_baseline", inputs=inputs, output_schema=self.schema, output_example=self.example,
            run_id=run_id, stage="S4", task_id="flat-plan", attempt=attempt, use_cache=False,
        )


def bind_task_planner_contract(invoker: AgentInvoker) -> TaskPlannerContractBinding:
    return TaskPlannerContractBinding(invoker)


def bind_plan_critic_contract(invoker: AgentInvoker) -> PlanCriticContractBinding:
    return PlanCriticContractBinding(invoker)


def bind_flat_plan_baseline_contract(invoker: AgentInvoker) -> FlatPlanBaselineContractBinding:
    return FlatPlanBaselineContractBinding(invoker)


def _parsed(result: Any) -> Any:
    value = getattr(result, "parsed", result)
    if isinstance(value, Mapping) and set(value) == {"parsed"}:
        return value["parsed"]
    return value


def validate_task_shard(
    shard: Mapping[str, Any],
    work_package: Mapping[str, Any],
    architecture: Mapping[str, Any],
    constraints: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one TaskPlanner result without requiring global Plan fields."""

    _require_schema(shard, "task-shard.schema.json", "TaskPlanner shard")
    if shard.get("work_package_id") != work_package.get("id"):
        raise S4ControlledError("task shard is bound to the wrong work package", code="S4_SHARD_SCOPE_INVALID")
    allowed_files = set(work_package.get("allowed_files", []))
    package_provides = set(work_package.get("provides_contracts", []))
    package_consumes = set(work_package.get("consumes_contracts", []))
    package_responsibilities = {item.get("req_id"): item.get("role") for item in work_package.get("requirement_responsibilities", [])}
    used_files: set[str] = set()
    local_ids: set[str] = set()
    provided: set[str] = set()
    consumed: set[str] = set()
    for task in shard["tasks"]:
        if _FORBIDDEN_KEYS.intersection(task):
            field = sorted(_FORBIDDEN_KEYS.intersection(task))[0]
            raise S4ControlledError(f"task shard contains forbidden field {field}", code="S4_SHARD_STATEFUL")
        local_id = task["local_id"]
        if local_id in local_ids:
            raise S4ControlledError(f"duplicate local task id {local_id!r}", code="S4_LOCAL_TASK_DUPLICATE")
        local_ids.add(local_id)
        files = set(task["deliverable_files"])
        if not files.issubset(allowed_files) or used_files.intersection(files) or len(files) > 4:
            raise S4ControlledError(f"task {local_id!r} does not form a local file partition", code="S4_SHARD_FILE_PARTITION")
        used_files.update(files)
        task_responsibility_pairs = [
            (item.get("req_id"), item.get("role"))
            for item in task.get("requirement_responsibilities", [])
        ]
        if len(task_responsibility_pairs) != len(set(task_responsibility_pairs)):
            raise S4ControlledError(f"task {local_id!r} repeats a responsibility", code="S4_SHARD_RESPONSIBILITY_INVALID")
        task_responsibilities = {item.get("req_id"): item.get("role") for item in task.get("requirement_responsibilities", [])}
        if any(req not in package_responsibilities or package_responsibilities[req] != role for req, role in task_responsibilities.items()):
            raise S4ControlledError(f"task {local_id!r} claims a package-external responsibility", code="S4_SHARD_RESPONSIBILITY_INVALID")
        provided.update(task.get("provides_contracts", []))
        consumed.update(task.get("consumes_contracts", []))
        if not set(task.get("provides_contracts", [])).issubset(package_provides) or not set(task.get("consumes_contracts", [])).issubset(package_consumes):
            raise S4ControlledError(f"task {local_id!r} claims a package-external contract", code="S4_SHARD_CONTRACT_INVALID")
        if any(dependency == local_id or (dependency not in local_ids and dependency not in {item["local_id"] for item in shard["tasks"]}) for dependency in task.get("depends_on", [])):
            raise S4ControlledError(f"task {local_id!r} has an unknown local dependency", code="S4_SHARD_DEPENDENCY_INVALID")
        variants = set(task["acceptance"]["build_variant_ids"])
        if constraints.get("build_variant_ids") and not variants.issubset(set(constraints["build_variant_ids"])):
            raise S4ControlledError(f"task {local_id!r} uses an unavailable build variant", code="S4_SHARD_BUILD_VARIANT_INVALID")
    if used_files != allowed_files:
        raise S4ControlledError("task shard does not cover every allowed package file", code="S4_SHARD_FILE_PARTITION")
    if provided != package_provides or consumed != package_consumes:
        raise S4ControlledError("task shard contract projections are incomplete", code="S4_SHARD_CONTRACT_PARTITION")
    for req_id, role in package_responsibilities.items():
        matches = [task for task in shard["tasks"] if any(item.get("req_id") == req_id and item.get("role") == role for item in task.get("requirement_responsibilities", []))]
        if not matches or (role == "primary" and len(matches) != 1):
            raise S4ControlledError(f"task shard does not refine responsibility {req_id!r}", code="S4_SHARD_RESPONSIBILITY_REFINEMENT")
    return {"valid": True, "work_package_id": work_package["id"], "task_count": len(shard["tasks"]), "files": sorted(used_files)}


def validate_plan_critic_result(result: Mapping[str, Any]) -> dict[str, Any]:
    _require_schema(result, "plan-critic-result.schema.json", "PlanCritic result")
    issues = list(result.get("issues", []))
    has_blocking = any(item["severity"] in {"blocker", "major"} for item in issues)
    if (result.get("verdict") == "revise") != has_blocking:
        raise S4ControlledError("PlanCritic verdict is inconsistent with its issue list", code="S4_CRITIC_VERDICT_INVALID")
    signatures = sorted({(item["severity"], item["scope"], item["target_id"], item["code"]) for item in issues})
    return {"verdict": result["verdict"], "issues": issues, "signatures": signatures}


def build_s4_commitment(
    run: Mapping[str, Any],
    prepared: PreparedArchitectureInputs,
    constraints: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the canonical S4a commitment from frozen run inputs."""

    config = run.get("config_snapshot")
    if not isinstance(config, Mapping) or run.get("config_snapshot_sha256") != config_snapshot_sha256(config):
        raise S4ControlledError("sealed configuration snapshot is invalid", code="S4_CONFIG_DRIFT")
    input_data = run.get("inputs")
    if not isinstance(input_data, Mapping):
        raise S4ControlledError("Run has no frozen input references", code="S4_INPUT_REF_INVALID")
    refs: dict[str, dict[str, str]] = {}
    # The public commitment points at the frozen run-local copies.  The
    # original source path in Run v3 may be absolute and is provenance only.
    for name, source_name, path in (
        ("spec", "spec", "spec/spec.json"),
        ("target_profile", "target_profile", "inputs/target.json"),
        ("test_bundle", "test_bundle", "inputs/test_bundle.json"),
    ):
        source = input_data.get(source_name)
        if not isinstance(source, Mapping) or not isinstance(source.get("sha256"), str):
            raise S4ControlledError(f"Run input {source_name} has no frozen hash", code="S4_INPUT_REF_INVALID")
        refs[name] = {"path": path, "sha256": source["sha256"]}
    requirements = [{"id": item["id"], "level": item["level"]} for item in sorted(prepared.spec["requirements"], key=lambda item: item["id"].encode("utf-8"))]
    budgets = config["budgets"]
    planning = config["planning"]
    commitment = {
        "schema_version": "1.0",
        "input_refs": refs,
        "config_snapshot_sha256": run["config_snapshot_sha256"],
        "strategy": planning["strategy"],
        "requirements": requirements,
        "test_manifest": copy.deepcopy(dict(manifest)),
        "build_variant_ids": sorted(constraints["build_variant_ids"]),
        "budgets": {
            "plan_architecture_repairs": budgets["plan_architecture_repairs"],
            "plan_task_shard_repairs": budgets["plan_task_shard_repairs"],
            "plan_critic_repairs": budgets["plan_critic_repairs"],
            "plan_global_replans": budgets["plan_global_replans"],
            "max_task_files": planning["max_task_files"],
            "context_safety_margin_ratio": planning["context_safety_margin_ratio"],
        },
        "layer_switches": {name: bool(config["stages"][name]) for name in ("l0", "l1", "l2", "l3")},
    }
    _require_schema(commitment, "s4-commitment.schema.json", "S4 commitment")
    return json.loads(canonical_json_bytes(commitment).decode("utf-8"))


def _input_refs_from_run(run: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    paths = {"spec": "spec/spec.json", "target_profile": "inputs/target.json", "test_bundle": "inputs/test_bundle.json"}
    return {name: {"path": path, "sha256": run["inputs"][name]["sha256"]} for name, path in paths.items()}


def _expand_layout_paths(architecture: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[str]:
    naming = constraints.get("naming", {})
    domains = {
        "messages": sorted(set((naming.get("message_ids") or {}).values()), key=lambda value: value.encode("utf-8")),
        "types": sorted(set((naming.get("type_ids") or {}).values()), key=lambda value: value.encode("utf-8")),
    }
    paths: list[str] = []
    for item in architecture.get("layout", {}).get("files", []):
        if item.get("path") is not None:
            paths.append(item["path"])
            continue
        domain = item.get("expand_over")
        placeholder = "{" + ("message_id" if domain == "messages" else "type_id") + "}"
        paths.extend(item["path_pattern"].replace(placeholder, value) for value in domains.get(domain, []))
    return sorted(set(paths), key=lambda value: value.encode("utf-8"))


def complete_plan_candidate(
    plan_draft_ir: Mapping[str, Any],
    constraints: Mapping[str, Any],
    frozen_refs: Mapping[str, Any],
    manifest: Mapping[str, Any],
    config_snapshot: Mapping[str, Any],
) -> CandidateCompletion:
    """Run the one common deterministic normalize/link/Blueprint/full-lint path."""

    source = copy.deepcopy(dict(plan_draft_ir))
    _require_schema(source, "plan-draft-ir.schema.json", "PlanDraftIR")
    spec = frozen_refs.get("spec_value") or frozen_refs.get("spec")
    target = frozen_refs.get("target_profile_value") or frozen_refs.get("target_profile")
    if not isinstance(spec, Mapping) or not isinstance(target, Mapping):
        raise S4ControlledError("complete_plan_candidate requires frozen Spec and Target values", code="S4_INPUT_REF_INVALID")
    refs_value = frozen_refs.get("refs") or frozen_refs.get("input_refs")
    if refs_value is None:
        refs_value = {name: frozen_refs[name] for name in ("spec", "target_profile", "test_bundle") if isinstance(frozen_refs.get(name), Mapping) and "path" in frozen_refs[name]}
    input_refs = {name: _safe_ref(refs_value[name], f"candidate input {name}") for name in ("spec", "target_profile", "test_bundle")}
    normalized = normalize_plan_draft(source["architecture"], source["work_packages"], source["task_shards"], constraints=constraints)
    linked = link_plan(
        normalized,
        constraints=constraints,
        spec=dict(spec),
        manifest=dict(manifest),
        config_snapshot=dict(config_snapshot),
        input_refs=input_refs,
    )
    lint_manifest = frozen_refs.get("test_bundle_value") or manifest
    # The shared lint API compares semantic companions using canonical JSON
    # bytes.  Preserve the run's raw frozen refs in the Plan, while linting a
    # canonical companion projection for this deterministic gate.
    lint_plan_value = copy.deepcopy(linked["plan"])
    lint_plan_value["input_refs"] = {
        "spec": {"path": input_refs["spec"]["path"], "sha256": _sha(canonical_json_bytes(dict(spec)))},
        "target_profile": {"path": input_refs["target_profile"]["path"], "sha256": _sha(canonical_json_bytes(dict(target)))},
        "test_bundle": {"path": input_refs["test_bundle"]["path"], "sha256": _sha(canonical_json_bytes(dict(lint_manifest)))},
    }
    lint_report = plan_lint(
        lint_plan_value, dict(spec), dict(lint_manifest), dict(config_snapshot), level="full",
        constraints=dict(constraints), blueprint=linked["blueprint"], target_profile=dict(target),
    )
    if not lint_report.get("valid"):
        raise S4ControlledError("candidate failed S4-G0 through S4-G6 full lint", code="S4_FULL_LINT_INVALID")
    return CandidateCompletion(
        plan_draft_ir=linked["plan_draft_ir"], plan=linked["plan"], blueprint=linked["blueprint"],
        link_report=linked["link_report"], lint_report=lint_report, constraints=dict(constraints),
        manifest=dict(manifest), spec=dict(spec), config_snapshot=dict(config_snapshot), input_refs=input_refs,
    )


def _ledger_paths(completion: CandidateCompletion) -> list[str]:
    expected = _expand_layout_paths(completion.plan["architecture"], completion.constraints)
    blueprint_paths: list[str] = []
    for rule in completion.blueprint.get("file_rules", []):
        pattern = rule.get("path_pattern")
        expansion = rule.get("expansion")
        if expansion == "per_message":
            domain = sorted(set((completion.constraints.get("naming", {}).get("message_ids") or {}).values()), key=lambda value: value.encode("utf-8"))
            blueprint_paths.extend(pattern.replace("{message_id}", value) for value in domain)
        elif expansion == "per_type":
            domain = sorted(set((completion.constraints.get("naming", {}).get("type_ids") or {}).values()), key=lambda value: value.encode("utf-8"))
            blueprint_paths.extend(pattern.replace("{type_id}", value) for value in domain)
        else:
            blueprint_paths.append(pattern)
    if sorted(set(blueprint_paths), key=lambda value: value.encode("utf-8")) != expected:
        raise S4ControlledError("Blueprint concrete file paths do not match the accepted layout", code="S4_LEDGER_PATH_INVALID")
    return expected


def _ledger_entries(completion: CandidateCompletion) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in completion.plan["architecture"].get("layout", {}).get("files", []):
        if item.get("path") is not None:
            paths = [item["path"]]
        else:
            domain_name = "message_ids" if item.get("expand_over") == "messages" else "type_ids"
            domain = sorted(set((completion.constraints.get("naming", {}).get(domain_name) or {}).values()), key=lambda value: value.encode("utf-8"))
            placeholder = "{message_id}" if item.get("expand_over") == "messages" else "{type_id}"
            paths = [item["path_pattern"].replace(placeholder, value) for value in domain]
        entries.extend({"path": path, "class": item["class"], "state": "slot_only"} for path in paths)
    return sorted(entries, key=lambda item: item["path"].encode("utf-8"))


def _contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(key in forbidden or _contains_forbidden_key(child, forbidden) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item, forbidden) for item in value)
    return False


def publish_initial_plan(
    store: RunStore,
    candidate_completion: CandidateCompletion,
    *,
    fault_hook: Any | None = None,
) -> StageResult:
    """Publish Plan 1.0.0, both initial ledgers, and the active pointer."""

    completion = candidate_completion
    _require_schema(completion.plan, "plan.schema.json", "initial Plan")
    if _contains_forbidden_key(completion.plan, {"task_uid", "obligation_digest", "guidance_digest"}):
        raise S4ControlledError("initial Plan contains M1-4d identity fields", code="S4_PLAN_SCOPE_INVALID")
    _ledger_paths(completion)
    plan_ref = store.publish_immutable_json("plan/versions/plan-1.0.0.json", completion.plan, schema_name="plan.schema.json")
    if fault_hook is not None:
        fault_hook("plan_published")
    file_ledger = {"schema_version": "1.0", "entries": _ledger_entries(completion)}
    _require_schema(file_ledger, "file-ledger.schema.json", "initial file ledger")
    file_ref = store.publish_immutable_json("plan/file_ledger.json", file_ledger, schema_name="file-ledger.schema.json")
    if fault_hook is not None:
        fault_hook("file_ledger_published")
    revision_ledger = {"schema_version": "1.0", "entries": []}
    _require_schema(revision_ledger, "revision-ledger.schema.json", "initial revision ledger")
    revision_ref = store.publish_immutable_json("plan/revision_ledger.json", revision_ledger, schema_name="revision-ledger.schema.json")
    if fault_hook is not None:
        fault_hook("revision_ledger_published")
    active = {"version": "1.0.0", "path": plan_ref.path, "sha256": plan_ref.sha256, "revision_seq": 0, "epoch": "E0"}
    _require_schema(active, "active-plan.schema.json", "active Plan pointer")
    active_ref = store.publish_immutable_json("plan/active_plan.json", active, schema_name="active-plan.schema.json")
    if fault_hook is not None:
        fault_hook("active_pointer_published")
    # Keep a stable, run-local semantic anchor for verify_completed.
    store.publish_immutable_json("plan/_s4/delivery_blueprint.json", completion.blueprint, schema_name="delivery-blueprint.schema.json")
    _verify_publication(store, completion, {"plan": plan_ref.as_dict(), "active_plan": active_ref.as_dict(), "delivery_blueprint_sha256": _sha(canonical_json_bytes(completion.blueprint)), "config_snapshot_sha256": config_snapshot_sha256(completion.config_snapshot)}, file_ref.as_dict(), revision_ref.as_dict())
    if fault_hook is not None:
        fault_hook("semantic_reread")
    return StageResult(output_refs={
        "plan": plan_ref.as_dict(), "active_plan": active_ref.as_dict(),
        "delivery_blueprint_sha256": _sha(canonical_json_bytes(completion.blueprint)),
        "config_snapshot_sha256": config_snapshot_sha256(completion.config_snapshot),
    })


def _verify_publication(
    store: RunStore,
    completion: CandidateCompletion,
    anchors: Mapping[str, Any],
    file_ref: Mapping[str, Any],
    revision_ref: Mapping[str, Any],
) -> None:
    store.verify_ref(anchors["plan"], schema_name="plan.schema.json")
    store.verify_ref(anchors["active_plan"], schema_name="active-plan.schema.json")
    store.verify_ref(file_ref, schema_name="file-ledger.schema.json")
    store.verify_ref(revision_ref, schema_name="revision-ledger.schema.json")
    plan = _json_ref(store.root, anchors["plan"], "published Plan", "plan.schema.json")
    pointer = _json_ref(store.root, anchors["active_plan"], "active pointer", "active-plan.schema.json")
    ledger = _json_ref(store.root, file_ref, "file ledger", "file-ledger.schema.json")
    revision = _json_ref(store.root, revision_ref, "revision ledger", "revision-ledger.schema.json")
    if plan != completion.plan or pointer != {"version": "1.0.0", "path": anchors["plan"]["path"], "sha256": anchors["plan"]["sha256"], "revision_seq": 0, "epoch": "E0"}:
        raise S4ArtifactDamage("published Plan or active pointer does not match the validated candidate")
    expected_ledger = {"schema_version": "1.0", "entries": _ledger_entries(completion)}
    if ledger != expected_ledger or revision != {"schema_version": "1.0", "entries": []}:
        raise S4ArtifactDamage("initial ledger content is not the validated canonical projection")
    blueprint_path = store._confined("plan/_s4/delivery_blueprint.json")
    if not blueprint_path.is_file():
        raise S4ArtifactDamage("published Delivery Blueprint is missing")
    blueprint_ref = {"path": "plan/_s4/delivery_blueprint.json", "sha256": _sha(blueprint_path.read_bytes())}
    persisted_blueprint = _json_ref(store.root, blueprint_ref, "published Delivery Blueprint", "delivery-blueprint.schema.json")
    if persisted_blueprint != completion.blueprint:
        raise S4ArtifactDamage("published Delivery Blueprint does not match the validated candidate")
    if anchors["delivery_blueprint_sha256"] != _sha(canonical_json_bytes(completion.blueprint)) or anchors["config_snapshot_sha256"] != config_snapshot_sha256(completion.config_snapshot):
        raise S4ArtifactDamage("initial publication anchors do not match the validated candidate")


class _CheckpointBook:
    STATE_PATH = "plan/_s4/s4_state.json"

    def __init__(self, store: RunStore, identity: Mapping[str, Any] | None = None) -> None:
        self.store = store
        if identity is None:
            run = store.load_run()
            prompts = _packaged_prompt_bytes(None)
            identity = {
                "input_refs": _input_refs_from_run(run),
                "config_snapshot_sha256": run["config_snapshot_sha256"],
                "prompt_sha256": {name: _sha(value) for name, value in prompts.items()},
                "strategy": run["config_snapshot"]["planning"]["strategy"],
            }
        state_path = self.store._confined(self.STATE_PATH)
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise S4ArtifactDamage("S4 control state is unreadable") from exc
            _require_schema(state, "s4-state.schema.json", "S4 control state")
            for key in ("input_refs", "config_snapshot_sha256", "prompt_sha256", "strategy"):
                if state[key] != identity[key]:
                    raise S4ArtifactDamage(f"S4 control state {key} drifted")
            for ref in state["accepted_refs"].values():
                checkpoint = _json_ref(
                    self.store.root, ref, "accepted S4 state checkpoint", "s4-checkpoint.schema.json"
                )
                self._validate_checkpoint(checkpoint, ref)
            self.state = state
        else:
            self.state = {
                "schema_version": "1.0",
                "phase": "prepare",
                "input_refs": copy.deepcopy(identity["input_refs"]),
                "config_snapshot_sha256": identity["config_snapshot_sha256"],
                "prompt_sha256": copy.deepcopy(identity["prompt_sha256"]),
                "strategy": identity["strategy"],
                "accepted_refs": {},
                "attempts": {"architecture": 0, "task_shards": {}, "critic": 0, "flat": 0},
                "repair_counters": {"architecture_repairs": 0, "critic_repairs": 0, "global_replans": 0},
                "task_shard_repairs": {},
                "pending_call": None,
                "seen_issue_signatures": [],
            }
            self._persist()

    @property
    def counters(self) -> dict[str, int]:
        return self.state["repair_counters"]

    def _persist(self) -> None:
        self.store.replace_json(self.STATE_PATH, self.state, schema_name="s4-state.schema.json")

    def set_phase(self, phase: str) -> None:
        self.state["phase"] = phase
        self._persist()

    def reserve_call(self, role: str, task_id: str | None, attempt: int) -> None:
        reservation = {"role": role, "task_id": task_id, "attempt": attempt}
        pending = self.state["pending_call"]
        if pending is not None and pending != reservation:
            raise S4ArtifactDamage("a different S4 Agent call is already pending")
        if pending is None:
            self.state["pending_call"] = reservation
            if role == "architecture_planner":
                self.state["attempts"]["architecture"] = max(self.state["attempts"]["architecture"], attempt)
            elif role == "task_planner" and task_id is not None:
                attempts = self.state["attempts"]["task_shards"]
                attempts[task_id] = max(attempts.get(task_id, 0), attempt)
            elif role == "plan_critic":
                self.state["attempts"]["critic"] = max(self.state["attempts"]["critic"], attempt)
            elif role == "flat_plan_baseline":
                self.state["attempts"]["flat"] = max(self.state["attempts"]["flat"], attempt)
            self._persist()

    def recover_pending_result(self, role: str, task_id: str | None, attempt: int) -> Any | None:
        if self.state["pending_call"] != {"role": role, "task_id": task_id, "attempt": attempt}:
            return None
        trace_path = self.store._confined("trace/llm_calls.ndjson")
        if not trace_path.is_file():
            return None
        try:
            rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise S4ArtifactDamage("LLM trace is unreadable during S4 recovery") from exc
        matches = [
            row for row in rows
            if row.get("stage") == "S4" and row.get("agent_role") == role
            and row.get("task_id") == task_id and row.get("attempt") == attempt
        ]
        if not matches:
            return None
        row = matches[-1]
        if row.get("validation") == "fail":
            raise S4ControlledError("the pending S4 Agent call previously failed", code="S4_STRUCTURED_OUTPUT_INVALID")
        output_path = row.get("output_path")
        if not isinstance(output_path, str):
            raise S4ArtifactDamage("completed S4 trace has no output artifact")
        try:
            output = json.loads(self.store.read_verified_bytes(output_path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RunStoreError) as exc:
            raise S4ArtifactDamage("completed S4 output evidence is unreadable") from exc
        if "parsed" not in output:
            raise S4ArtifactDamage("completed S4 output evidence has no parsed result")
        return SimpleNamespace(parsed=output["parsed"])

    def record_signatures(self, signatures: set[tuple[str, str, str, str]]) -> None:
        merged = {tuple(item) for item in self.state["seen_issue_signatures"]}
        merged.update(signatures)
        self.state["seen_issue_signatures"] = [list(item) for item in sorted(merged)]
        self._persist()

    def _paths(self) -> list[Path]:
        root = self.store._confined("plan/_s4/checkpoints")
        if not root.is_dir():
            return []
        return sorted(root.glob("*.json"), key=lambda path: path.name)

    def _validate_checkpoint(
        self,
        value: Mapping[str, Any],
        ref: Mapping[str, Any],
        *,
        visiting: set[tuple[str, str]] | None = None,
    ) -> None:
        """Validate the immutable reference graph without selecting a state branch."""

        safe = _safe_ref(ref, "checkpoint")
        current = (safe["path"], safe["sha256"])
        active = set() if visiting is None else set(visiting)
        if current in active:
            raise S4ControlledError("S4 checkpoint parent cycle", code="S4_CHECKPOINT_INVALID")
        active.add(current)
        for child in value["payload_refs"] + value["report_refs"]:
            _read_ref(self.store.root, child, "checkpoint child")
        for parent in value["parent_refs"]:
            parent_ref = _safe_ref(parent, "checkpoint parent")
            parent_value = _json_ref(self.store.root, parent_ref, "checkpoint parent", "s4-checkpoint.schema.json")
            if int(parent_value["ordinal"]) >= int(value["ordinal"]):
                raise S4ControlledError("S4 checkpoint parent is not earlier than its child", code="S4_CHECKPOINT_INVALID")
            self._validate_checkpoint(parent_value, parent_ref, visiting=active)

    def consume(self, name: str, limit: int, *, target_id: str | None = None) -> None:
        if name == "task_shard_repairs":
            if target_id is None:
                raise S4Error("task shard repair budget requires a work-package id", code="S4_CHECKPOINT_INVALID")
            used = int(self.state["task_shard_repairs"].get(target_id, 0))
            if used >= limit:
                raise S4ControlledError(f"S4 semantic budget exhausted: {name}:{target_id}", code="S4_BUDGET_EXHAUSTED")
            self.state["task_shard_repairs"][target_id] = used + 1
        else:
            if self.counters[name] >= limit:
                raise S4ControlledError(f"S4 semantic budget exhausted: {name}", code="S4_BUDGET_EXHAUSTED")
            self.counters[name] += 1
        self._persist()

    def task_repairs_used(self, target_id: str) -> int:
        return int(self.state["task_shard_repairs"].get(target_id, 0))

    def publish(self, *, kind: str, target_id: str, parents: list[Mapping[str, Any]], payloads: list[Mapping[str, Any]], reports: list[Mapping[str, Any]]) -> dict[str, str]:
        ordinal = 0
        for path in self._paths():
            try:
                ordinal = max(ordinal, int(path.stem.split("-", 1)[0]))
            except (ValueError, IndexError):
                continue
        ordinal += 1
        checkpoint = {
            "schema_version": "1.0", "kind": kind, "ordinal": ordinal, "target_id": target_id,
            "parent_refs": [dict(_safe_ref(item, "checkpoint parent")) for item in parents],
            "payload_refs": [dict(_safe_ref(item, "checkpoint payload")) for item in payloads],
            "report_refs": [dict(_safe_ref(item, "checkpoint report")) for item in reports],
            "budget_counters": {
                "architecture_repairs": self.counters["architecture_repairs"],
                "task_shard_repairs": sum(int(value) for value in self.state["task_shard_repairs"].values()),
                "critic_repairs": self.counters["critic_repairs"],
                "global_replans": self.counters["global_replans"],
            },
        }
        _require_schema(checkpoint, "s4-checkpoint.schema.json", "S4 checkpoint")
        safe_target = re.sub(r"[^A-Za-z0-9_-]+", "-", target_id).strip("-") or "target"
        ref = self.store.publish_immutable_json(f"plan/_s4/checkpoints/{ordinal:04d}-{kind}-{safe_target}.json", checkpoint, schema_name="s4-checkpoint.schema.json")
        result = ref.as_dict()
        self.state["accepted_refs"][f"{kind}:{target_id}"] = result
        self.state["pending_call"] = None
        self.state["phase"] = {
            "commitment": "prepare", "architecture_attempt": "architecture", "architecture_sealed": "architecture",
            "shard_attempt": "shards", "candidate": "candidate", "review": "review",
        }[kind]
        self._persist()
        return result

    def find(self, *, kind: str, target_id: str | None = None, parents: list[Mapping[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, str]] | None:
        matches: list[tuple[int, dict[str, Any], dict[str, str]]] = []
        expected_parents = [dict(item) for item in parents] if parents is not None else None
        for path in self._paths():
            ref = {"path": path.relative_to(self.store.root).as_posix(), "sha256": _sha(path.read_bytes())}
            try:
                value = _json_ref(self.store.root, ref, "checkpoint", "s4-checkpoint.schema.json")
                self._validate_checkpoint(value, ref)
            except S4ArtifactDamage:
                raise
            except S4Error:
                continue
            if value.get("kind") != kind or (target_id is not None and value.get("target_id") != target_id):
                continue
            if expected_parents is not None and value.get("parent_refs") != expected_parents:
                continue
            matches.append((int(value["ordinal"]), value, ref))
        if not matches:
            return None
        _, value, ref = max(matches, key=lambda item: item[0])
        return value, ref


def _checkpoint_payload(store: RunStore, checkpoint: Mapping[str, Any], index: int, schema_name: str | None = None) -> dict[str, Any]:
    refs = checkpoint.get("payload_refs", [])
    if index >= len(refs):
        raise S4ArtifactDamage("checkpoint is missing a required payload reference")
    return _json_ref(store.root, refs[index], "checkpoint payload", schema_name)


def _publish_s4_json(store: RunStore, path: str, value: Any, schema_name: str | None = None) -> dict[str, str]:
    return store.publish_immutable_json(path, value, schema_name=schema_name).as_dict()


def _route_context_limits(invoker: AgentInvoker, context_window_tokens: Mapping[str, int] | None) -> dict[str, int]:
    from ..agents.base import resolve_route

    route = resolve_route(invoker.config, "architecture_planner")
    if context_window_tokens is None:
        return {route.model: 128000}
    if route.model in context_window_tokens:
        return {route.model: int(context_window_tokens[route.model])}
    if len(context_window_tokens) == 1:
        return {route.model: int(next(iter(context_window_tokens.values())))}
    return {str(key): int(value) for key, value in context_window_tokens.items()}


class S4Controller:
    """Bounded S4a/S4b/S4c production controller."""

    def __init__(
        self,
        invoker: AgentInvoker,
        *,
        handoff_root: str | Path = DEFAULT_HANDOFF_ROOT,
        packaged_prompts: Mapping[str, Any] | None = None,
        context_window_tokens: Mapping[str, int] | None = None,
        fault_hook: Any | None = None,
    ) -> None:
        self.invoker = invoker
        self.handoff_root = Path(handoff_root)
        self.packaged_prompts = packaged_prompts
        self.context_window_tokens = context_window_tokens
        self.fault_hook = fault_hook
        self.architecture_binding = ArchitecturePlannerContractBinding(invoker)
        self.task_binding = TaskPlannerContractBinding(invoker)
        self.critic_binding = PlanCriticContractBinding(invoker)
        self.flat_binding = FlatPlanBaselineContractBinding(invoker)

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    def _controlled(self, exc: BaseException, code: str = "S4_FAILED") -> ControlledStageFailure:
        actual = getattr(exc, "code", code)
        return ControlledStageFailure({"code": actual, "detail": str(exc)})

    def _prepare(self, context: StageContext) -> tuple[PreparedArchitectureInputs, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str], _CheckpointBook]:
        context.store.verify_frozen_inputs()
        run = context.store.load_run()
        prepared = prepare_architecture_inputs(
            context.store._confined("spec/spec.json").read_bytes(),
            context.store._confined("inputs/target.json").read_bytes(),
            context.store._confined("inputs/test_bundle.json").read_bytes(),
        )
        constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
        manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
        planning_index = build_planning_index(prepared, manifest, constraints)
        commitment = build_s4_commitment(run, prepared, constraints, manifest)
        commitment_ref = _publish_s4_json(context.store, "plan/_s4/commitment.json", commitment, "s4-commitment.schema.json")
        _publish_s4_json(context.store, "plan/_s4/planning_index.json", planning_index)
        _publish_s4_json(context.store, "plan/_s4/delivery_constraints.json", constraints)
        _publish_s4_json(context.store, "plan/_s4/test_manifest_metadata.json", manifest)
        prompt_bytes = _packaged_prompt_bytes(self.packaged_prompts)
        book = _CheckpointBook(context.store, {
            "input_refs": commitment["input_refs"],
            "config_snapshot_sha256": commitment["config_snapshot_sha256"],
            "prompt_sha256": {name: _sha(value) for name, value in prompt_bytes.items()},
            "strategy": commitment["strategy"],
        })
        existing = book.find(kind="commitment", target_id="commitment", parents=[])
        if existing is None:
            commitment_checkpoint_ref = book.publish(kind="commitment", target_id="commitment", parents=[], payloads=[commitment_ref], reports=[])
        else:
            commitment_checkpoint_ref = existing[1]
        self._fault("s4_commitment_published")
        return prepared, constraints, manifest, planning_index, commitment_checkpoint_ref, book

    def _preflight_architecture(self, planning_index: Mapping[str, Any], constraints: Mapping[str, Any], repair_context: Any, *, repair: bool) -> None:
        schema, example = self.architecture_binding.contract("patch" if repair else "full_draft", 1 if repair else 0)
        from ..agents.base import resolve_route

        route = resolve_route(self.invoker.config, "architecture_planner")
        report = architecture_planner_context_preflight(
            planning_index, constraints, model_limits=_route_context_limits(self.invoker, self.context_window_tokens),
            requested_output_tokens=route.max_tokens, safety_margin_ratio=self.invoker.config.planning.context_safety_margin_ratio,
            output_schema=schema, output_example=example, repair_context=repair_context,
            template_bytes=_packaged_prompt_bytes(self.packaged_prompts)["repair" if repair else "initial"],
        )
        # This is diagnostic evidence, not a new state authority.
        self._preflight_report = report

    def _admit_and_invoke(self, context: StageContext, invoke: Any) -> Any:
        context.orchestrator.admit_external_call(context.store)
        return invoke()

    def _invoke_reserved(
        self,
        context: StageContext,
        book: _CheckpointBook,
        *,
        role: str,
        task_id: str | None,
        attempt: int,
        invoke: Any,
    ) -> Any:
        book.reserve_call(role, task_id, attempt)
        recovered = book.recover_pending_result(role, task_id, attempt)
        if recovered is not None:
            return recovered
        return self._admit_and_invoke(context, invoke)

    def _architecture_validate(self, draft: Mapping[str, Any], planning_index: Mapping[str, Any], manifest: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return validate_architecture(draft, planning_index, manifest, constraints)
        except ArchitectureError as exc:
            raise S4ControlledError(str(exc), code="S4_ARCHITECTURE_INVALID") from exc

    def _publish_architecture_attempt(self, store: RunStore, book: _CheckpointBook, target: str, parent_refs: list[Mapping[str, Any]], draft: Mapping[str, Any], validation: Mapping[str, Any], patch: Mapping[str, Any] | None = None, application: Mapping[str, Any] | None = None) -> dict[str, Any]:
        base = f"plan/_s4/architecture/attempt_{target}"
        candidate_ref = _publish_s4_json(store, f"{base}/candidate.json", draft, "architecture-draft.schema.json")
        validation_ref = _publish_s4_json(store, f"{base}/validation.json", validation, "architecture-validation.schema.json")
        payloads = [candidate_ref]
        reports = [validation_ref]
        if patch is not None:
            payloads.append(_publish_s4_json(store, f"{base}/patch.json", patch, "architecture-patch.schema.json"))
        if application is not None:
            payloads.append(_publish_s4_json(store, f"{base}/application.json", application, "architecture-draft.schema.json"))
        checkpoint_ref = book.publish(kind="architecture_attempt", target_id=target, parents=parent_refs, payloads=payloads, reports=reports)
        return {"candidate_ref": candidate_ref, "validation_ref": validation_ref, "checkpoint_ref": checkpoint_ref}

    def _architecture(self, context: StageContext, prepared: PreparedArchitectureInputs, constraints: dict[str, Any], manifest: dict[str, Any], planning_index: dict[str, Any], commitment_ref: dict[str, str], book: _CheckpointBook) -> tuple[dict[str, Any], dict[str, str]]:
        admission = verify_m1_4a2_handoff(self.handoff_root, self.packaged_prompts)
        sealed = book.find(kind="architecture_sealed", target_id="architecture")
        if sealed is not None:
            checkpoint, checkpoint_ref = sealed
            return _checkpoint_payload(context.store, checkpoint, 0, "architecture-draft.schema.json"), checkpoint_ref
        attempt = book.find(kind="architecture_attempt", target_id="architecture", parents=[commitment_ref])
        if attempt is None:
            self._preflight_architecture(planning_index, constraints, None, repair=False)
            _publish_s4_json(context.store, "plan/_s4/preflight/architecture_initial.json", self._preflight_report)
            result = self._invoke_reserved(context, book, role="architecture_planner", task_id="architecture", attempt=1, invoke=lambda: self.architecture_binding.invoke(
                planning_index=planning_index, delivery_constraints=constraints, repair_context=None,
                run_id=context.run["run_id"], task_id="architecture", attempt=1, use_cache=False,
                template_bytes=admission.initial_bytes, repair_mode="full_draft", depth=0, phase="initial",
            ))
            draft = load_architecture_draft(_parsed(result))
            validation = self._architecture_validate(draft, planning_index, manifest, constraints)
            self._publish_architecture_attempt(context.store, book, "architecture", [commitment_ref], draft, validation)
            self._fault("s4_architecture_initial_published")
            attempt = (book.find(kind="architecture_attempt", target_id="architecture", parents=[commitment_ref]))
            if attempt is None:
                raise S4Error("architecture checkpoint disappeared after publication", code="S4_CHECKPOINT_INVALID")
        checkpoint, checkpoint_ref = attempt
        draft = _checkpoint_payload(context.store, checkpoint, 0, "architecture-draft.schema.json")
        validation = _json_ref(context.store.root, checkpoint["report_refs"][0], "architecture validation", "architecture-validation.schema.json")
        if validation.get("verdict") != "pass":
            config = context.run["config_snapshot"]
            repair_parents = [commitment_ref, checkpoint_ref]
            repair_attempt = book.find(kind="architecture_attempt", target_id="architecture-repair", parents=repair_parents)
            if repair_attempt is None:
                limit = int(config["budgets"]["plan_architecture_repairs"])
                book.consume("architecture_repairs", limit)
                try:
                    locality = map_architecture_failures_to_paths(draft, validation.get("issues", []))
                except Exception as exc:
                    raise S4ControlledError(str(exc), code="S4_ARCHITECTURE_REPAIR_INVALID") from exc
                repair_context = {"candidate": draft, "validation_issues": validation.get("issues", []), "allowed_paths": locality["allowed_paths"]}
                self._preflight_architecture(planning_index, constraints, repair_context, repair=True)
                _publish_s4_json(context.store, "plan/_s4/preflight/architecture_repair.json", self._preflight_report)
                result = self._invoke_reserved(context, book, role="architecture_planner", task_id="architecture", attempt=2, invoke=lambda: self.architecture_binding.invoke(
                    planning_index=planning_index, delivery_constraints=constraints, repair_context=repair_context,
                    run_id=context.run["run_id"], task_id="architecture", attempt=2, use_cache=False,
                    template_bytes=admission.repair_bytes, repair_mode="patch", depth=1, phase="repair",
                ))
                patch = _parsed(result)
                try:
                    repaired, _projection = apply_architecture_patch_with_projection(draft, patch, locality["allowed_paths"], constraints)
                except Exception as exc:
                    raise S4ControlledError(str(exc), code="S4_ARCHITECTURE_REPAIR_INVALID") from exc
                validation = self._architecture_validate(repaired, planning_index, manifest, constraints)
                self._publish_architecture_attempt(context.store, book, "architecture-repair", repair_parents, repaired, validation, patch=patch, application=repaired)
                self._fault("s4_architecture_repair_published")
                repair_attempt = book.find(kind="architecture_attempt", target_id="architecture-repair", parents=repair_parents)
                if repair_attempt is None:
                    raise S4Error("architecture repair checkpoint disappeared after publication", code="S4_CHECKPOINT_INVALID")
            checkpoint, checkpoint_ref = repair_attempt
            draft = _checkpoint_payload(context.store, checkpoint, 0, "architecture-draft.schema.json")
            validation = _json_ref(context.store.root, checkpoint["report_refs"][0], "architecture repair validation", "architecture-validation.schema.json")
        if validation.get("verdict") != "pass":
            raise S4ControlledError("ArchitecturePlanner did not close arch_01 through arch_15", code="S4_ARCHITECTURE_INVALID")
        sealed_ref = book.publish(kind="architecture_sealed", target_id="architecture", parents=[checkpoint_ref], payloads=[checkpoint["payload_refs"][0]], reports=[checkpoint["report_refs"][0]])
        self._fault("s4_architecture_sealed")
        return draft, sealed_ref

    def _global_architecture_repair(
        self,
        context: StageContext,
        draft: Mapping[str, Any],
        current_sealed_ref: Mapping[str, Any],
        planning_index: Mapping[str, Any],
        manifest: Mapping[str, Any],
        constraints: Mapping[str, Any],
        book: _CheckpointBook,
        issues: list[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        allowed = ["/decisions", "/contracts", "/modules", "/work_packages", "/layout"]
        repair_context = {"candidate": dict(draft), "validation_issues": [dict(item) for item in issues], "allowed_paths": allowed}
        admission = verify_m1_4a2_handoff(self.handoff_root, self.packaged_prompts)
        self._preflight_architecture(planning_index, constraints, repair_context, repair=True)
        _publish_s4_json(context.store, "plan/_s4/preflight/architecture_global_repair.json", self._preflight_report)
        result = self._invoke_reserved(context, book, role="architecture_planner", task_id="architecture", attempt=3, invoke=lambda: self.architecture_binding.invoke(
            planning_index=planning_index, delivery_constraints=constraints, repair_context=repair_context,
            run_id=context.run["run_id"], task_id="architecture", attempt=3, use_cache=False,
            template_bytes=admission.repair_bytes, repair_mode="patch", depth=1, phase="repair",
        ))
        patch = _parsed(result)
        try:
            repaired, _projection = apply_architecture_patch_with_projection(draft, patch, allowed, constraints)
        except Exception as exc:
            raise S4ControlledError(str(exc), code="S4_ARCHITECTURE_REPAIR_INVALID") from exc
        validation = self._architecture_validate(repaired, planning_index, manifest, constraints)
        if validation.get("verdict") != "pass":
            raise S4ControlledError("global ArchitecturePlanner repair did not close architecture", code="S4_ARCHITECTURE_INVALID")
        target = "architecture-global-001"
        self._publish_architecture_attempt(context.store, book, target, [dict(current_sealed_ref)], repaired, validation, patch=patch, application=repaired)
        self._fault("s4_architecture_global_repair_published")
        attempt = book.find(kind="architecture_attempt", target_id=target, parents=[dict(current_sealed_ref)])
        if attempt is None:
            raise S4Error("global architecture checkpoint disappeared after publication", code="S4_CHECKPOINT_INVALID")
        checkpoint, attempt_ref = attempt
        sealed_ref = book.publish(kind="architecture_sealed", target_id="architecture", parents=[attempt_ref], payloads=[checkpoint["payload_refs"][0]], reports=[checkpoint["report_refs"][0]])
        return repaired, sealed_ref

    def _task_inputs(
        self,
        package: Mapping[str, Any],
        architecture: Mapping[str, Any],
        planning_index: Mapping[str, Any],
        manifest: Mapping[str, Any],
        constraints: Mapping[str, Any],
        book: _CheckpointBook,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        req_ids = {item.get("req_id") for item in package.get("requirement_responsibilities", [])}
        requirements = [item for item in planning_index.get("requirements", []) if item.get("id") in req_ids]
        # A responsibility-bearing slice includes all structural elements that
        # name one of those requirements, without exposing test source/code.
        types = [item for item in planning_index.get("types", []) if req_ids.intersection(item.get("req_ids", []))]
        messages = [item for item in planning_index.get("messages", []) if req_ids.intersection(item.get("req_ids", [])) or any(req_ids.intersection(field.get("req_ids", [])) for field in item.get("fields", []))]
        module_id = package.get("module")
        contracts = [item for item in architecture.get("contracts", []) if item.get("owner") == module_id or module_id in item.get("consumers", []) or item.get("id") in set(package.get("provides_contracts", [])) | set(package.get("consumes_contracts", []))]
        test_metadata = [item for item in manifest.get("tests", []) if req_ids.intersection(item.get("req_ids", []))]
        return {
            "work_package": copy.deepcopy(dict(package)),
            "spec_slice": {"requirements": requirements, "types": types, "messages": messages},
            "adjacent_contracts": sorted((copy.deepcopy(dict(item)) for item in contracts), key=lambda item: item.get("id", "").encode("utf-8")),
            "test_metadata": {"schema_version": manifest.get("schema_version"), "bundle": copy.deepcopy(manifest.get("bundle")), "tests": test_metadata, "build_variant_ids": list(manifest.get("build_variant_ids", []))},
            "planning_budget": {
                "max_task_files": config["planning"]["max_task_files"],
                "context_safety_margin_ratio": config["planning"]["context_safety_margin_ratio"],
                "task_shard_repairs_remaining": max(0, int(config["budgets"]["plan_task_shard_repairs"]) - book.task_repairs_used(str(package["id"]))),
            },
        }

    def _publish_shard(
        self,
        store: RunStore,
        book: _CheckpointBook,
        package_id: str,
        attempt_name: str,
        parents: list[Mapping[str, Any]],
        shard: Mapping[str, Any],
        report: Mapping[str, Any],
    ) -> tuple[dict[str, str], dict[str, str]]:
        base = f"plan/_s4/shards/{package_id}/{attempt_name}"
        shard_ref = _publish_s4_json(store, f"{base}/task_shard.json", shard, "task-shard.schema.json")
        report_ref = _publish_s4_json(store, f"{base}/validation.json", dict(report))
        checkpoint_ref = book.publish(kind="shard_attempt", target_id=package_id, parents=parents, payloads=[shard_ref], reports=[report_ref])
        return checkpoint_ref, shard_ref

    def _shard(
        self,
        context: StageContext,
        package: Mapping[str, Any],
        architecture: Mapping[str, Any],
        planning_index: Mapping[str, Any],
        manifest: Mapping[str, Any],
        constraints: Mapping[str, Any],
        sealed_ref: Mapping[str, Any],
        book: _CheckpointBook,
        *,
        force_redo: bool = False,
        budget_reserved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        package_id = str(package["id"])
        initial = book.find(kind="shard_attempt", target_id=package_id, parents=[dict(sealed_ref)])
        if initial is not None and not force_redo:
            checkpoint, checkpoint_ref = initial
            shard = _checkpoint_payload(context.store, checkpoint, 0, "task-shard.schema.json")
            try:
                validate_task_shard(shard, package, architecture, constraints)
                report = _json_ref(context.store.root, checkpoint["report_refs"][0], "task shard validation")
                if report.get("valid") is True:
                    return shard, checkpoint_ref
            except S4Error:
                pass
        else:
            checkpoint_ref = None

        parent_refs = [dict(sealed_ref)]
        if initial is not None:
            parent_refs.append(initial[1])
            retry = book.find(kind="shard_attempt", target_id=package_id, parents=parent_refs)
            if retry is not None:
                try:
                    retry_checkpoint, retry_ref = retry
                    retry_shard = _checkpoint_payload(context.store, retry_checkpoint, 0, "task-shard.schema.json")
                    validate_task_shard(retry_shard, package, architecture, constraints)
                    return retry_shard, retry_ref
                except S4ControlledError as exc:
                    raise S4ControlledError("task shard remained invalid after its bounded redo", code="S4_SHARD_INVALID") from exc
        else:
            retry = None
        if force_redo and initial is None:
            raise S4ControlledError("cannot redo a missing task shard", code="S4_CHECKPOINT_INVALID")
        attempt_number = 2 if initial is not None else 1
        config = context.run["config_snapshot"]
        if initial is not None and not budget_reserved:
            # Reserve the package-local semantic redo before provider I/O so a
            # prior invalid shard cannot silently reset the allowance.
            book.consume("task_shard_repairs", int(config["budgets"]["plan_task_shard_repairs"]), target_id=package_id)
        inputs = self._task_inputs(package, architecture, planning_index, manifest, constraints, book, config)
        result = self._invoke_reserved(
            context, book, role="task_planner", task_id=package_id, attempt=attempt_number,
            invoke=lambda: self.task_binding.invoke(inputs=inputs, run_id=context.run["run_id"], task_id=package_id, attempt=attempt_number),
        )
        shard = _parsed(result)
        try:
            validate_task_shard(shard, package, architecture, constraints)
            report = {"valid": True, "work_package_id": package_id}
        except S4ControlledError as exc:
            report = {"valid": False, "work_package_id": package_id, "code": exc.code, "detail": str(exc)}
            # A semantic redo is cumulative and package-local.
            if initial is None:
                self._publish_shard(context.store, book, package_id, "attempt_001", [dict(sealed_ref)], shard, report)
                initial = book.find(kind="shard_attempt", target_id=package_id, parents=[dict(sealed_ref)])
            if initial is None:
                raise S4Error("invalid shard checkpoint disappeared", code="S4_CHECKPOINT_INVALID")
            if attempt_number == 1 and not force_redo:
                book.consume("task_shard_repairs", int(config["budgets"]["plan_task_shard_repairs"]), target_id=package_id)
                return self._shard(
                    context, package, architecture, planning_index, manifest, constraints, sealed_ref, book,
                    force_redo=True, budget_reserved=True,
                )
            self._publish_shard(context.store, book, package_id, "attempt_002", parent_refs, shard, report)
            raise S4ControlledError("task shard remained invalid after its bounded redo", code="S4_SHARD_INVALID")
        checkpoint_ref, _shard_ref = self._publish_shard(context.store, book, package_id, f"attempt_{attempt_number:03d}", parent_refs, shard, report)
        found = book.find(kind="shard_attempt", target_id=package_id, parents=parent_refs)
        self._fault("s4_shard_published")
        if found is None:
            raise S4Error("task shard checkpoint disappeared after publication", code="S4_CHECKPOINT_INVALID")
        return shard, found[1]

    def _flat_draft(
        self,
        context: StageContext,
        planning_index: Mapping[str, Any],
        constraints: Mapping[str, Any],
        manifest: Mapping[str, Any],
        book: _CheckpointBook,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        existing = book.find(kind="architecture_attempt", target_id="flat")
        if existing is not None:
            checkpoint, checkpoint_ref = existing
            return _checkpoint_payload(context.store, checkpoint, 0, "flat-plan-draft.schema.json"), checkpoint_ref
        result = self._invoke_reserved(context, book, role="flat_plan_baseline", task_id="flat-plan", attempt=1, invoke=lambda: self.flat_binding.invoke(
            inputs={"planning_index": planning_index, "delivery_constraints": constraints, "manifest_metadata": manifest},
            run_id=context.run["run_id"], attempt=1,
        ))
        draft = _parsed(result)
        _require_schema(draft, "flat-plan-draft.schema.json", "FlatPlanBaseline draft")
        draft_ref = _publish_s4_json(context.store, "plan/_s4/flat/attempt_001/draft.json", draft, "flat-plan-draft.schema.json")
        report_ref = _publish_s4_json(context.store, "plan/_s4/flat/attempt_001/validation.json", {"valid": True})
        checkpoint_ref = book.publish(kind="architecture_attempt", target_id="flat", parents=[], payloads=[draft_ref], reports=[report_ref])
        self._fault("s4_flat_draft_published")
        return draft, checkpoint_ref

    def _publish_candidate(
        self,
        store: RunStore,
        book: _CheckpointBook,
        completion: CandidateCompletion,
        parents: list[Mapping[str, Any]],
    ) -> tuple[CandidateCompletion, dict[str, str]]:
        round_number = 1
        candidate_root = store._confined("plan/_s4/candidates")
        if candidate_root.is_dir():
            for path in candidate_root.glob("round_*"):
                try:
                    round_number = max(round_number, int(path.name.split("_", 1)[1]) + 1)
                except (ValueError, IndexError):
                    continue
        base = f"plan/_s4/candidates/round_{round_number:03d}"
        draft_ref = _publish_s4_json(store, f"{base}/plan_draft_ir.json", completion.plan_draft_ir, "plan-draft-ir.schema.json")
        link_ref = _publish_s4_json(store, f"{base}/link_report.json", completion.link_report, "link-report.schema.json")
        blueprint_ref = _publish_s4_json(store, f"{base}/blueprint.json", completion.blueprint, "delivery-blueprint.schema.json")
        plan_ref = _publish_s4_json(store, f"{base}/plan.json", completion.plan, "plan.schema.json")
        lint_ref = _publish_s4_json(store, f"{base}/full_lint.json", completion.lint_report)
        checkpoint_ref = book.publish(
            kind="candidate", target_id=f"round_{round_number:03d}", parents=[dict(item) for item in parents],
            payloads=[draft_ref, link_ref, blueprint_ref, plan_ref], reports=[lint_ref],
        )
        self._fault("s4_candidate_published")
        return completion, checkpoint_ref

    def _load_candidate(self, store: RunStore, checkpoint: Mapping[str, Any], context: StageContext, constraints: Mapping[str, Any], manifest: Mapping[str, Any], spec: Mapping[str, Any], config: Mapping[str, Any], input_refs: Mapping[str, Mapping[str, str]]) -> CandidateCompletion:
        payloads = checkpoint.get("payload_refs", [])
        if len(payloads) != 4 or len(checkpoint.get("report_refs", [])) != 1:
            raise S4ArtifactDamage("candidate checkpoint does not contain the complete evidence set")
        draft = _json_ref(store.root, payloads[0], "PlanDraftIR", "plan-draft-ir.schema.json")
        link_report = _json_ref(store.root, payloads[1], "link report", "link-report.schema.json")
        blueprint = _json_ref(store.root, payloads[2], "Delivery Blueprint", "delivery-blueprint.schema.json")
        plan = _json_ref(store.root, payloads[3], "candidate Plan", "plan.schema.json")
        lint_report = _json_ref(store.root, checkpoint["report_refs"][0], "full lint report")
        if not lint_report.get("valid"):
            raise S4ArtifactDamage("candidate checkpoint has an invalid full lint report")
        return CandidateCompletion(
            plan_draft_ir=draft, plan=plan, blueprint=blueprint, link_report=link_report, lint_report=lint_report,
            constraints=dict(constraints), manifest=dict(manifest), spec=dict(spec), config_snapshot=dict(config), input_refs={key: dict(value) for key, value in input_refs.items()},
        )

    def _review_history(self, store: RunStore, book: _CheckpointBook) -> set[tuple[str, str, str, str]]:
        signatures: set[tuple[str, str, str, str]] = set()
        for path in book._paths():
            try:
                checkpoint = json.loads(path.read_text(encoding="utf-8"))
                if checkpoint.get("kind") != "review":
                    continue
                review = _checkpoint_payload(store, checkpoint, 0, "plan-critic-result.schema.json")
                for item in review.get("issues", []):
                    if item.get("severity") in {"blocker", "major"}:
                        signatures.add((item["severity"], item["scope"], item["target_id"], item["code"]))
            except (S4Error, KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                continue
        return signatures

    def _critic(
        self,
        context: StageContext,
        completion: CandidateCompletion,
        candidate_ref: Mapping[str, Any],
        round_id: str,
        book: _CheckpointBook,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        existing = book.find(kind="review", target_id=round_id, parents=[dict(candidate_ref)])
        if existing is not None:
            checkpoint, checkpoint_ref = existing
            return _checkpoint_payload(context.store, checkpoint, 0, "plan-critic-result.schema.json"), checkpoint_ref
        try:
            critic_attempt = int(round_id.rsplit("_", 1)[1])
        except (IndexError, ValueError) as exc:
            raise S4Error("candidate round id is invalid", code="S4_CHECKPOINT_INVALID") from exc
        result = self._invoke_reserved(context, book, role="plan_critic", task_id="plan-critic", attempt=critic_attempt, invoke=lambda: self.critic_binding.invoke(
            inputs={"candidate_plan_graph": completion.plan, "coverage_matrix": completion.plan["coverage"], "lint_report": completion.lint_report},
            run_id=context.run["run_id"], task_id="plan-critic", attempt=critic_attempt,
        ))
        review = _parsed(result)
        validated = validate_plan_critic_result(review)
        review_ref = _publish_s4_json(context.store, f"plan/_s4/reviews/{round_id}/result.json", review, "plan-critic-result.schema.json")
        checkpoint_ref = book.publish(kind="review", target_id=round_id, parents=[dict(candidate_ref)], payloads=[review_ref], reports=[])
        self._fault("s4_review_published")
        return review, checkpoint_ref

    @staticmethod
    def _with_review(completion: CandidateCompletion, issues: list[Mapping[str, Any]]) -> CandidateCompletion:
        plan = copy.deepcopy(completion.plan)
        plan["review"] = {"verdict": "pass", "unresolved_minor_issues": [dict(item) for item in issues]}
        return replace(completion, plan=plan)

    @staticmethod
    def _issue_package(plan: Mapping[str, Any], issue: Mapping[str, Any], architecture: Mapping[str, Any]) -> str | None:
        target = issue.get("target_id")
        if issue.get("scope") == "work_package":
            return target if any(item.get("id") == target for item in architecture.get("work_packages", [])) else None
        if issue.get("scope") == "task":
            for task in plan.get("tasks", []):
                if task.get("id") == target:
                    return task.get("work_package")
        return None

    def _candidate_and_review(
        self,
        context: StageContext,
        prepared: PreparedArchitectureInputs,
        constraints: dict[str, Any],
        manifest: dict[str, Any],
        planning_index: dict[str, Any],
        book: _CheckpointBook,
        commitment_ref: dict[str, str],
    ) -> CandidateCompletion:
        config = context.run["config_snapshot"]
        strategy = config["planning"]["strategy"]
        input_refs = _input_refs_from_run(context.run)
        frozen_refs = {"spec_value": prepared.spec, "target_profile_value": prepared.target_profile, "test_bundle_value": prepared.test_bundle, "refs": input_refs}
        if strategy == "layered":
            architecture, sealed_ref = self._architecture(context, prepared, constraints, manifest, planning_index, commitment_ref, book)
            shards: dict[str, dict[str, Any]] = {}
            shard_refs: dict[str, dict[str, str]] = {}
            for package in sorted(architecture.get("work_packages", []), key=lambda item: item["id"].encode("utf-8")):
                shard, shard_ref = self._shard(context, package, architecture, planning_index, manifest, constraints, sealed_ref, book)
                shards[package["id"]] = shard
                shard_refs[package["id"]] = shard_ref
            draft = {"schema_version": "1.0", "architecture": architecture, "work_packages": architecture["work_packages"], "task_shards": [shards[key] for key in sorted(shards, key=lambda value: value.encode("utf-8"))]}
            parents = [sealed_ref] + [shard_refs[key] for key in sorted(shard_refs, key=lambda value: value.encode("utf-8"))]
        elif strategy == "flat":
            draft, flat_checkpoint_ref = self._flat_draft(context, planning_index, constraints, manifest, book)
            parents = [flat_checkpoint_ref]
            architecture = draft["architecture"]
            shards = {item["work_package_id"]: item for item in draft["task_shards"]}
        else:
            raise S4ControlledError(f"unsupported sealed planning strategy {strategy!r}", code="S4_STRATEGY_INVALID")

        seen = self._review_history(context.store, book)
        while True:
            candidate_checkpoint = book.find(kind="candidate", parents=parents)
            if candidate_checkpoint is None:
                candidate = complete_plan_candidate(draft, constraints, frozen_refs, manifest, config)
                candidate, candidate_ref = self._publish_candidate(context.store, book, candidate, parents)
                candidate_checkpoint = book.find(kind="candidate", parents=parents)
                if candidate_checkpoint is None:
                    raise S4Error("candidate checkpoint disappeared after publication", code="S4_CHECKPOINT_INVALID")
            else:
                candidate, candidate_ref = self._load_candidate(
                    context.store, candidate_checkpoint[0], context, constraints, manifest, prepared.spec, config, input_refs,
                ), candidate_checkpoint[1]
            round_id = candidate_checkpoint[0]["target_id"]
            existing_review = book.find(kind="review", target_id=round_id, parents=[dict(candidate_ref)])
            review, _review_ref = self._critic(context, candidate, candidate_ref, round_id, book)
            validated = validate_plan_critic_result(review)
            issues = list(validated["issues"])
            major = [item for item in issues if item["severity"] in {"blocker", "major"}]
            if not major:
                return self._with_review(candidate, [item for item in issues if item["severity"] == "minor"])
            signatures = set(validated["signatures"])
            # A review checkpoint may be the last durable record before its
            # repair call.  It is the current decision, not a repeated one;
            # only earlier review signatures establish non-convergence.
            if existing_review is not None:
                seen.difference_update(signatures)
            if signatures.intersection(seen):
                raise S4ControlledError("PlanCritic repeated a blocker/major issue signature", code="S4_CRITIC_NON_CONVERGENT")
            seen.update(signatures)
            book.record_signatures(signatures)
            book.consume("critic_repairs", int(config["budgets"]["plan_critic_repairs"]))
            if strategy == "flat":
                book.consume("global_replans", int(config["budgets"]["plan_global_replans"]))
                # Flat is a single explicit arm; revise the whole semantic
                # draft and never route the failure through layered roles.
                flat_attempt = book.counters["global_replans"] + 1
                result = self._invoke_reserved(context, book, role="flat_plan_baseline", task_id="flat-plan", attempt=flat_attempt, invoke=lambda: self.flat_binding.invoke(
                    inputs={"planning_index": planning_index, "delivery_constraints": constraints, "manifest_metadata": manifest},
                    run_id=context.run["run_id"], attempt=flat_attempt,
                ))
                draft = _parsed(result)
                _require_schema(draft, "flat-plan-draft.schema.json", "FlatPlanBaseline repair")
                flat_path = f"plan/_s4/flat/attempt_{book.counters['global_replans'] + 1:03d}/draft.json"
                flat_ref = _publish_s4_json(context.store, flat_path, draft, "flat-plan-draft.schema.json")
                flat_report = _publish_s4_json(context.store, f"plan/_s4/flat/attempt_{book.counters['global_replans'] + 1:03d}/validation.json", {"valid": True})
                flat_checkpoint_ref = book.publish(kind="architecture_attempt", target_id="flat", parents=[flat_checkpoint_ref], payloads=[flat_ref], reports=[flat_report])
                self._fault("s4_flat_repair_published")
                parents = [flat_checkpoint_ref]
                continue
            package_ids = {self._issue_package(candidate.plan, item, architecture) for item in major}
            package_ids.discard(None)
            is_local = package_ids and all(item["scope"] in {"work_package", "task"} for item in major) and len(package_ids) == 1
            if is_local:
                package_id = next(iter(package_ids))
                package = next(item for item in architecture["work_packages"] if item["id"] == package_id)
                # A local redo is attached to the current architecture seal;
                # the older shard remains immutable evidence but is not reused.
                shard, shard_ref = self._shard(context, package, architecture, planning_index, manifest, constraints, sealed_ref, book, force_redo=True)
                shards[package_id] = shard
                shard_refs[package_id] = shard_ref
                draft = {"schema_version": "1.0", "architecture": architecture, "work_packages": architecture["work_packages"], "task_shards": [shards[key] for key in sorted(shards, key=lambda value: value.encode("utf-8"))]}
                parents = [sealed_ref] + [shard_refs[key] for key in sorted(shard_refs, key=lambda value: value.encode("utf-8"))]
                continue
            book.consume("global_replans", int(config["budgets"]["plan_global_replans"]))
            architecture, sealed_ref = self._global_architecture_repair(context, architecture, sealed_ref, planning_index, manifest, constraints, book, major)
            shards = {}
            shard_refs = {}
            for package in sorted(architecture.get("work_packages", []), key=lambda item: item["id"].encode("utf-8")):
                shard, shard_ref = self._shard(context, package, architecture, planning_index, manifest, constraints, sealed_ref, book)
                shards[package["id"]] = shard
                shard_refs[package["id"]] = shard_ref
            draft = {"schema_version": "1.0", "architecture": architecture, "work_packages": architecture["work_packages"], "task_shards": [shards[key] for key in sorted(shards, key=lambda value: value.encode("utf-8"))]}
            parents = [sealed_ref] + [shard_refs[key] for key in sorted(shard_refs, key=lambda value: value.encode("utf-8"))]

    def _verify_seal_artifacts(self, store: RunStore, output_refs: Mapping[str, Any], run: Mapping[str, Any]) -> None:
        expected = {"plan", "active_plan", "delivery_blueprint_sha256", "config_snapshot_sha256"}
        if set(output_refs) != expected:
            raise S4ArtifactDamage("S4 seal output_refs are incomplete")
        store.verify_stage_refs({"output_refs": output_refs}, "s4")
        plan = _json_ref(store.root, output_refs["plan"], "sealed Plan", "plan.schema.json")
        active = _json_ref(store.root, output_refs["active_plan"], "sealed active pointer", "active-plan.schema.json")
        if active != {"version": "1.0.0", "path": output_refs["plan"]["path"], "sha256": output_refs["plan"]["sha256"], "revision_seq": 0, "epoch": "E0"}:
            raise S4ArtifactDamage("active pointer does not target the sealed Plan")
        if output_refs["config_snapshot_sha256"] != run["config_snapshot_sha256"]:
            raise S4ArtifactDamage("sealed configuration hash disagrees with Run v3")
        constraints = _json_ref(store.root, {"path": "plan/_s4/delivery_constraints.json", "sha256": _sha(store._confined("plan/_s4/delivery_constraints.json").read_bytes())}, "sealed Delivery Constraints")
        blueprint_path = store._confined("plan/_s4/delivery_blueprint.json")
        if not blueprint_path.is_file():
            raise S4ArtifactDamage("sealed Delivery Blueprint evidence is missing")
        blueprint = _json_ref(store.root, {"path": "plan/_s4/delivery_blueprint.json", "sha256": _sha(blueprint_path.read_bytes())}, "sealed Delivery Blueprint", "delivery-blueprint.schema.json")
        recomputed = compile_delivery_blueprint(constraints, plan["architecture"], plan["work_packages"], plan["tasks"])
        if blueprint != recomputed or output_refs["delivery_blueprint_sha256"] != _sha(canonical_json_bytes(recomputed)):
            raise S4ArtifactDamage("sealed Delivery Blueprint anchor does not recompute")
        input_refs = _input_refs_from_run(run)
        if plan.get("input_refs") != input_refs:
            raise S4ArtifactDamage("sealed Plan input refs do not bind the frozen Run inputs")
        ledger = _json_ref(store.root, {"path": "plan/file_ledger.json", "sha256": _sha(store._confined("plan/file_ledger.json").read_bytes())}, "sealed file ledger", "file-ledger.schema.json")
        revision = _json_ref(store.root, {"path": "plan/revision_ledger.json", "sha256": _sha(store._confined("plan/revision_ledger.json").read_bytes())}, "sealed revision ledger", "revision-ledger.schema.json")
        expected_entries = []
        for item in plan["architecture"].get("layout", {}).get("files", []):
            if item.get("path") is not None:
                item_paths = [item["path"]]
            else:
                domain_name = "message_ids" if item.get("expand_over") == "messages" else "type_ids"
                domain = sorted(set((constraints.get("naming", {}).get(domain_name) or {}).values()), key=lambda value: value.encode("utf-8"))
                placeholder = "{message_id}" if item.get("expand_over") == "messages" else "{type_id}"
                item_paths = [item["path_pattern"].replace(placeholder, value) for value in domain]
            expected_entries.extend({"path": path, "class": item["class"], "state": "slot_only"} for path in item_paths)
        expected_entries.sort(key=lambda item: item["path"].encode("utf-8"))
        if ledger != {"schema_version": "1.0", "entries": expected_entries} or revision != {"schema_version": "1.0", "entries": []}:
            raise S4ArtifactDamage("sealed initial ledgers do not match the Blueprint")
        spec = _json_ref(store.root, {"path": "spec/spec.json", "sha256": run["inputs"]["spec"]["sha256"]}, "sealed Spec")
        bundle = _json_ref(store.root, {"path": "inputs/test_bundle.json", "sha256": run["inputs"]["test_bundle"]["sha256"]}, "sealed Test Bundle")
        lint_plan_value = copy.deepcopy(plan)
        lint_plan_value["input_refs"] = {
            "spec": {"path": plan["input_refs"]["spec"]["path"], "sha256": _sha(canonical_json_bytes(spec))},
            "target_profile": {"path": plan["input_refs"]["target_profile"]["path"], "sha256": _sha(canonical_json_bytes(constraints["target_profile"]))},
            "test_bundle": {"path": plan["input_refs"]["test_bundle"]["path"], "sha256": _sha(canonical_json_bytes(bundle))},
        }
        report = plan_lint(lint_plan_value, spec, bundle, run["config_snapshot"], level="full", constraints=constraints, blueprint=blueprint, target_profile=constraints["target_profile"])
        if not report.get("valid"):
            raise S4ArtifactDamage("sealed Plan no longer passes full lint")

    def verify_result(self, store: RunStore, result: StageResult) -> None:
        """Verify the publication suffix before Orchestrator commits S4."""

        run = store.load_run()
        if not isinstance(result.output_refs, Mapping):
            raise S4ArtifactDamage("S4 result has no output refs")
        self._verify_seal_artifacts(store, result.output_refs, run)

    def verify_completed(self, store: RunStore) -> None:
        """Semantically re-read a committed S4 seal; this method is read-only."""

        run = store.load_run()
        stage = run["stages"]["s4"]
        if stage.get("status") != "done":
            raise S4ArtifactDamage("cannot verify an S4 stage that is not done")
        self._verify_seal_artifacts(store, stage.get("output_refs", {}), run)

    def run(self, context: StageContext) -> StageResult:
        try:
            prepared, constraints, manifest, planning_index, commitment_ref, book = self._prepare(context)
            completion = self._candidate_and_review(context, prepared, constraints, manifest, planning_index, book, commitment_ref)
            # Regenerate the final state-free candidate after the final review;
            # this keeps publication independent of any review-side mutation.
            final_draft = completion.plan_draft_ir
            review = completion.plan.get("review", {"verdict": "pass", "unresolved_minor_issues": []})
            completion = complete_plan_candidate(final_draft, constraints, {"spec_value": prepared.spec, "target_profile_value": prepared.target_profile, "test_bundle_value": prepared.test_bundle, "refs": _input_refs_from_run(context.run)}, manifest, context.run["config_snapshot"])
            completion = replace(completion, plan={**completion.plan, "review": review})
            book.set_phase("publication")
            published = publish_initial_plan(context.store, completion, fault_hook=self._fault)
            book.set_phase("sealed")
            return published
        except ControlledStageFailure:
            raise
        except StructuredOutputError as exc:
            raise self._controlled(exc, "S4_STRUCTURED_OUTPUT_INVALID") from exc
        except (S4ControlledError, PlanningInputError, PlanningContextError, PlanError, DeliveryConstraintError, ArchitectureError) as exc:
            raise self._controlled(exc) from exc


__all__ = [
    "ApprovedArchitecturePromptBundle", "CandidateCompletion", "FlatPlanBaselineContractBinding", "LINEAGE_ID",
    "PlanCriticContractBinding", "S4ArtifactDamage", "S4ControlledError", "S4Controller", "S4Error",
    "TaskPlannerContractBinding", "bind_flat_plan_baseline_contract", "bind_plan_critic_contract",
    "bind_task_planner_contract", "build_s4_commitment", "complete_plan_candidate", "publish_initial_plan",
    "validate_plan_critic_result", "validate_task_shard", "verify_m1_4a2_handoff",
]
