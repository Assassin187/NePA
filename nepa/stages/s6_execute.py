"""S6 single-task implementation loop with evidence-bound git commits."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nepa.agents.base import AgentRunner, TruncatedOutputError
from nepa.agents.contracts import CODER_OUTPUT_SCHEMA, DIAGNOSER_OUTPUT_SCHEMA
from nepa.canonical import atomic_write_canonical_json, canonical_sha256
from nepa.llm.client import StructuredOutputError
from nepa.plan_state import (
    AttemptsExhaustedEvent,
    AttemptStartedEvent,
    AttemptSucceededEvent,
    DependencyBlockedEvent,
    execution_state_lint,
    plan_state_snapshot_lint,
    publish_initial_plan_state,
    transition_plan_state,
)
from nepa.reconciliation import (
    ReconciliationError,
    audit_done_tasks,
    reconcile_in_progress_task,
)
from nepa.round_store import RoundStore
from nepa.run_store import RunStore
from nepa.speclib.slice import resolve_refs
from nepa.stages.s5_scaffold import GateRunner
from nepa.task_evidence import publish_task_evidence, task_evidence_relative_path
from nepa.test_summary import build_test_summary
from nepa.tools.build import BuildResults, BuildTool
from nepa.tools.fs_ops import (
    UnsafePathError,
    load_json,
    resolve_workspace_path,
    sha256_file,
    write_allowed_files,
)
from nepa.tools.git_ops import GitError, GitOps

__all__ = ["S6Error", "S6Inputs", "S6Result", "execute_plan"]


class S6Error(RuntimeError):
    """S6 controlled failure with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class S6Inputs:
    spec: dict[str, Any]
    language: dict[str, Any]
    test_bundle: dict[str, Any]
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class S6Result:
    state: dict[str, Any]
    workspace_head: str
    published: bool


def _load_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise S6Error("EXECUTION_INPUT_INVALID", f"{description}: {exc}") from exc
    if not isinstance(value, dict):
        raise S6Error("EXECUTION_INPUT_INVALID", f"{description} root must be object")
    return value


def _stage_receipts(store: RunStore) -> dict[str, Any]:
    return {
        stage: state.output_refs
        for stage, state in store.meta.stages.items()
        if state.output_refs is not None
    }


def _state_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(item for item in state["tasks"] if item["id"] == task_id)


def _quarantine_orphan_evidence(
    store: RunStore,
    *,
    task_id: str,
    attempt: int,
) -> None:
    path = store.run_dir / task_evidence_relative_path(task_id, attempt)
    if not path.exists():
        return
    target_dir = store.run_dir / "cache" / "s6-orphan-evidence"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{task_id}-attempt-{attempt:03d}.json"
    index = 1
    while target.exists():
        target = target_dir / f"{task_id}-attempt-{attempt:03d}-{index}.json"
        index += 1
    os.replace(path, target)


def _admit(
    store: RunStore,
    *,
    plan: dict[str, Any],
    inputs: S6Inputs,
) -> tuple[dict[str, Any], GitOps]:
    s4_seal = store.meta.stages["s4"].output_refs or {}
    s5_seal = store.meta.stages["s5"].output_refs or {}
    if not s4_seal or not s5_seal:
        raise S6Error("EXECUTION_INPUT_INVALID", "S4/S5 receipts are required")
    workspace = store.run_dir / "workspace"
    git = GitOps(workspace)
    state_path = store.run_dir / "plan" / "plan_state.json"
    if not state_path.exists():
        baseline = s5_seal.get("workspace_head")
        if (
            not isinstance(baseline, str)
            or git.head() != baseline
            or not git.is_clean()
        ):
            raise S6Error(
                "EXECUTION_INPUT_INVALID",
                "fresh S6 admission requires the clean S5 baseline",
            )
        state = publish_initial_plan_state(
            state_path,
            plan,
            s4_seal,
            store.meta.config_snapshot,
        )
    else:
        state = _load_object(state_path, "Plan State")
        snapshot = plan_state_snapshot_lint(
            plan,
            state,
            s4_seal,
            store.meta.config_snapshot,
        )
        if not snapshot.ok:
            raise S6Error(
                "EXECUTION_STATE_INVALID",
                str(sorted(snapshot.error_codes())),
            )
        try:
            audit_done_tasks(
                git,
                store.run_dir,
                state,
                plan_sha256=canonical_sha256(plan),
            )
            for task_state in state["tasks"]:
                if task_state["status"] != "in_progress":
                    continue
                task_id = str(task_state["id"])
                reconciled = reconcile_in_progress_task(
                    git,
                    store.run_dir,
                    state_path,
                    task_id,
                    plan=plan,
                    s4_seal=s4_seal,
                    config_snapshot=store.meta.config_snapshot,
                )
                if not reconciled:
                    task = next(item for item in plan["tasks"] if item["id"] == task_id)
                    git.restore_files(task["deliverable_files"])
                    _quarantine_orphan_evidence(
                        store,
                        task_id=task_id,
                        attempt=int(task_state["attempts"]),
                    )
            state = _load_object(state_path, "reconciled Plan State")
        except ReconciliationError as exc:
            raise S6Error("EXECUTION_RECONCILIATION_FAILED", str(exc)) from exc
    report = execution_state_lint(
        plan,
        state,
        git,
        store.run_dir,
        _stage_receipts(store),
        test_bundle=inputs.test_bundle,
    )
    if not report.ok:
        raise S6Error("EXECUTION_STATE_INVALID", str(sorted(report.error_codes())))
    return state, git


def _interface_context(
    workspace: Path,
    task: dict[str, Any],
    contract_map: dict[str, Any],
) -> list[dict[str, Any]]:
    relevant = set(task["provides_contracts"]) | set(task["consumes_contracts"])
    contracts = [
        deepcopy(item)
        for item in contract_map["contracts"]
        if item["id"] in relevant
    ]
    paths = sorted(
        {
            str(path)
            for item in contracts
            for path in item.get("interface_files", [])
        }
    )
    interfaces = []
    for relative in paths:
        path = resolve_workspace_path(workspace, relative)
        if path.is_file():
            interfaces.append(
                {"path": relative, "content": path.read_text(encoding="utf-8")}
            )
    return [{"contracts": contracts, "files": interfaces}]


def _build_context(
    *,
    plan: dict[str, Any],
    state: dict[str, Any],
    task: dict[str, Any],
    inputs: S6Inputs,
    contract_map: dict[str, Any],
    workspace: Path,
    feedback: dict[str, Any] | None,
) -> dict[str, Any]:
    package = next(
        item for item in plan["work_packages"] if item["id"] == task["work_package"]
    )
    current_files = []
    for relative in task["deliverable_files"]:
        path = resolve_workspace_path(workspace, relative)
        current_files.append(
            {
                "path": relative,
                "content": path.read_text(encoding="utf-8") if path.is_file() else "",
            }
        )
    return {
        "task": deepcopy(task),
        "work_package": deepcopy(package),
        "architecture_decisions": deepcopy(plan["architecture"]["decisions"]),
        "spec_slice": resolve_refs(inputs.spec, task["context_refs"]),
        "interface_context": _interface_context(workspace, task, contract_map),
        "language_profile": deepcopy(inputs.language),
        "current_files": current_files,
        "task_state": deepcopy(_state_task(state, task["id"])),
        "latest_feedback": deepcopy(feedback),
    }


def _build_results(value: BuildResults) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant_id, result in (("release", value.release), ("san", value.sanitizer)):
        if result is None:
            continue
        passed = result.code == 0 and not result.timed_out
        row: dict[str, Any] = {
            "variant_id": variant_id,
            "result": "pass" if passed else ("error" if result.timed_out else "fail"),
            "duration_ms": result.duration_ms,
            "warnings": 0,
            "errors": 0 if passed else 1,
        }
        if not passed:
            row["output_excerpt"] = (
                result.stderr or result.stdout or "build failed"
            )[-4000:]
        rows.append(row)
    return rows


def _publish_attempt_results(
    store: RunStore,
    *,
    task: dict[str, Any],
    attempt: int,
    plan: dict[str, Any],
    inputs: S6Inputs,
    workspace_head: str,
    workspace_tree: str,
    build_results: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    build_relative = (
        f"test_results/build_results/{task['id']}/attempt_{attempt:03d}.json"
    )
    build_path = store.run_dir / build_relative
    atomic_write_canonical_json(
        build_path,
        {
            "schema_version": "1.0",
            "task_id": task["id"],
            "attempt": attempt,
            "workspace_tree": workspace_tree,
            "results": build_results,
        },
    )
    build_ref = {"path": build_relative, "sha256": sha256_file(build_path)}
    rounds = RoundStore(store.run_dir)
    index = rounds.load_index()
    round_id = len(index["rounds"]) + 1
    parent = index["rounds"][-1]["round_id"] if index["rounds"] else None
    summary = build_test_summary(
        round_id=round_id,
        trigger="s6_task",
        task_id=task["id"],
        attempt=attempt,
        workspace_head=workspace_head,
        workspace_tree=workspace_tree,
        parent_round_id=parent,
        plan_sha256=canonical_sha256(plan),
        delivery_blueprint_sha256=plan["delivery_blueprint_sha256"],
        manifest_sha256=inputs.test_bundle["manifest_ref"]["sha256"],
        bundle_tree_sha256=inputs.test_bundle["bundle_tree_sha256"],
        build_results=build_results,
        cases=cases,
    )
    entry = rounds.publish_round(
        summary,
        stage="s6",
        producer_context={"task_id": task["id"], "attempt": attempt},
    )
    return build_ref, entry["summary_ref"]


def _failure_excerpt(
    build_results: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> str:
    for item in [*build_results, *cases]:
        excerpt = item.get("output_excerpt")
        if isinstance(excerpt, str) and excerpt:
            return excerpt[-4000:]
    return "task acceptance failed"


def execute_plan(
    store: RunStore,
    inputs: S6Inputs,
    runner: AgentRunner,
    *,
    build_tool: BuildTool,
    gate_runner: GateRunner,
) -> S6Result:
    """Execute Plan tasks in stable order until every task is done or blocked."""
    if store.meta.stages["s6"].status == "done":
        refs = store.meta.stages["s6"].output_refs or {}
        state_ref = refs.get("plan_state")
        if not isinstance(state_ref, dict):
            raise S6Error("EXECUTION_RECEIPT_INVALID", "missing Plan State receipt")
        path = store.run_dir / str(state_ref.get("path"))
        if not path.is_file() or sha256_file(path) != state_ref.get("sha256"):
            raise S6Error("EXECUTION_RECEIPT_INVALID", "Plan State receipt drift")
        state = _load_object(path, "sealed Plan State")
        git = GitOps(store.run_dir / "workspace")
        if git.head() != refs.get("workspace_head") or not git.is_clean():
            raise S6Error("EXECUTION_RECEIPT_INVALID", "workspace receipt drift")
        return S6Result(state=state, workspace_head=git.head(), published=False)

    store.begin_stage("s6")
    plan = _load_object(store.run_dir / "plan" / "plan.json", "Plan")
    contract_map = _load_object(
        store.run_dir / "plan" / "contract_map.json",
        "Contract Map",
    )
    state, git = _admit(store, plan=plan, inputs=inputs)
    state_path = store.run_dir / "plan" / "plan_state.json"
    s4_seal = store.meta.stages["s4"].output_refs or {}
    workspace = store.run_dir / "workspace"
    t2_limit = int(store.meta.config_snapshot["budgets"]["task_fix_attempts"])
    total_limit = t2_limit + 1

    for task in plan["tasks"]:
        task_id = str(task["id"])
        task_state = _state_task(state, task_id)
        if task_state["status"] in {"done", "blocked", "blocked_by_dependency"}:
            continue
        dependencies = {
            item["id"]: item["status"] for item in state["tasks"]
        }
        if any(
            dependencies.get(dependency) in {"blocked", "blocked_by_dependency"}
            for dependency in task["depends_on"]
        ):
            state = transition_plan_state(
                state_path,
                task_id,
                DependencyBlockedEvent(),
                plan=plan,
                s4_seal=s4_seal,
                config_snapshot=store.meta.config_snapshot,
            )
            continue

        feedback: dict[str, Any] | None = None
        if task_state["status"] == "in_progress":
            feedback = {
                "root_cause": "The previous process stopped before a valid task commit.",
                "suspect_files": list(task["deliverable_files"]),
                "fix_guidance": "Re-run the task from the restored committed baseline.",
            }
        last_error = ""
        while int(_state_task(state, task_id)["attempts"]) < total_limit:
            old = _state_task(state, task_id)
            previous = last_error or (
                "resumed incomplete attempt" if old["status"] == "in_progress" else None
            )
            state = transition_plan_state(
                state_path,
                task_id,
                AttemptStartedEvent(previous_error=previous),
                plan=plan,
                s4_seal=s4_seal,
                config_snapshot=store.meta.config_snapshot,
            )
            attempt = int(_state_task(state, task_id)["attempts"])
            tier = "T2" if attempt <= t2_limit else "T1"
            payload = _build_context(
                plan=plan,
                state=state,
                task=task,
                inputs=inputs,
                contract_map=contract_map,
                workspace=workspace,
                feedback=feedback,
            )
            role = "coder" if feedback is None else "fixer"
            try:
                output = runner.invoke(
                    role,
                    payload,
                    CODER_OUTPUT_SCHEMA,
                    stage="s6",
                    task_id=task_id,
                    attempt=attempt,
                    tier_override=tier,
                )
                write_allowed_files(
                    workspace,
                    output["files"],
                    set(task["deliverable_files"]),
                )
                build = build_tool.both(workspace)
                build_rows = _build_results(build)
                cases: list[dict[str, Any]] = []
                if build.ok:
                    selected = tuple(
                        item
                        for item in inputs.manifest["tests"]
                        if item["nodeid"] in task["acceptance"]["tests"]
                    )
                    cases = gate_runner(workspace, selected)
                clean = build_tool.sandbox.exec(
                    ["make", "clean"],
                    str(workspace),
                    timeout_s=60,
                )
                if clean.code != 0:
                    raise S6Error("EXECUTION_BUILD_CLEAN_FAILED", clean.stderr)
                prepared = git.prepare_task_commit(task["deliverable_files"])
                build_ref, summary_ref = _publish_attempt_results(
                    store,
                    task=task,
                    attempt=attempt,
                    plan=plan,
                    inputs=inputs,
                    workspace_head=git.head(),
                    workspace_tree=prepared.workspace_tree,
                    build_results=build_rows,
                    cases=cases,
                )
                passed = build.ok and all(
                    item.get("result") == "pass" for item in cases
                )
                if passed:
                    evidence = publish_task_evidence(
                        store.run_dir,
                        task_id=task_id,
                        attempt=attempt,
                        plan_sha256=canonical_sha256(plan),
                        workspace_tree=prepared.workspace_tree,
                        build_result_refs=[build_ref],
                        test_summary_refs=[summary_ref],
                    )
                    commit = git.commit_prepared_task(
                        prepared,
                        task_id=task_id,
                        title=task["title"],
                        attempt=attempt,
                        evidence_sha256=evidence.ref["sha256"],
                    )
                    state = transition_plan_state(
                        state_path,
                        task_id,
                        AttemptSucceededEvent(
                            commit_sha=commit,
                            task_evidence_ref=evidence.ref,
                            notes=str(output["notes"]),
                        ),
                        plan=plan,
                        s4_seal=s4_seal,
                        config_snapshot=store.meta.config_snapshot,
                    )
                    break
                last_error = _failure_excerpt(build_rows, cases)
                git.restore_files(task["deliverable_files"])
                feedback = runner.invoke(
                    "diagnoser",
                    {
                        "task": task,
                        "failure_excerpt": last_error,
                        "current_files": payload["current_files"],
                    },
                    DIAGNOSER_OUTPUT_SCHEMA,
                    stage="s6",
                    task_id=task_id,
                    attempt=attempt,
                    tier_override=tier,
                )
            except S6Error:
                git.restore_files(task["deliverable_files"])
                raise
            except (
                GitError,
                StructuredOutputError,
                TruncatedOutputError,
                UnsafePathError,
                TypeError,
                ValueError,
            ) as exc:
                git.restore_files(task["deliverable_files"])
                last_error = str(exc)
                feedback = {
                    "root_cause": "The structured task attempt failed before acceptance.",
                    "suspect_files": list(task["deliverable_files"]),
                    "fix_guidance": last_error[-2000:],
                }

        if _state_task(state, task_id)["status"] != "done":
            state = transition_plan_state(
                state_path,
                task_id,
                AttemptsExhaustedEvent(
                    detail=last_error or "task attempts exhausted",
                    notes="No accepted task commit was produced.",
                ),
                plan=plan,
                s4_seal=s4_seal,
                config_snapshot=store.meta.config_snapshot,
            )

    if any(item["status"] != "done" for item in state["tasks"]):
        raise S6Error("EXECUTION_TASKS_BLOCKED", "one or more tasks did not complete")
    report = execution_state_lint(
        plan,
        state,
        git,
        store.run_dir,
        _stage_receipts(store),
        test_bundle=inputs.test_bundle,
    )
    if not report.ok:
        raise S6Error("EXECUTION_STATE_INVALID", str(sorted(report.error_codes())))
    state_ref = {
        "path": "plan/plan_state.json",
        "sha256": sha256_file(state_path),
    }
    store.set_stage_status(
        "s6",
        "done",
        output_refs={"plan_state": state_ref, "workspace_head": git.head()},
    )
    return S6Result(state=state, workspace_head=git.head(), published=True)
