"""Plan State v1 Schema、初始化与 snapshot lint 测试（设计 5.2.4～5.2.5）。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from nepa.canonical import canonical_json_bytes, canonical_sha256
from nepa.plan_state import (
    AttemptsExhaustedEvent,
    AttemptStartedEvent,
    AttemptSucceededEvent,
    DependencyBlockedEvent,
    PlanStateValidationError,
    ReconciledCommitEvent,
    ReconciliationProof,
    initialize_plan_state,
    plan_state_snapshot_lint,
    publish_initial_plan_state,
    transition_plan_state,
    validate_state_transition,
)


def _plan() -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "tasks": [
            {"id": "T-001", "depends_on": []},
            {"id": "T-002", "depends_on": ["T-001"]},
        ],
    }


def _config() -> dict[str, Any]:
    return {"budgets": {"task_fix_attempts": 3}}


def _seal(plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan": {
            "path": "plan/plan.json",
            "sha256": canonical_sha256(plan),
        },
        "delivery_blueprint_sha256": "ab" * 32,
        "config_snapshot_sha256": canonical_sha256(config),
    }


def _state() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = _plan()
    config = _config()
    seal = _seal(plan, config)
    return plan, config, seal, initialize_plan_state(plan, seal, config)


def test_initializer_builds_exact_all_pending_task_set() -> None:
    plan, config, seal, state = _state()

    assert state["plan_ref"] == seal["plan"]
    assert [task["id"] for task in state["tasks"]] == ["T-001", "T-002"]
    assert all(
        task
        == {
            "id": task["id"],
            "status": "pending",
            "attempts": 0,
            "notes": "",
            "commit_sha": None,
            "last_error": None,
            "acceptance_evidence": {"task_evidence_ref": None},
        }
        for task in state["tasks"]
    )
    assert plan_state_snapshot_lint(plan, state, seal, config).ok


def test_publish_is_canonical_and_refuses_reinitialization(tmp_path: Path) -> None:
    plan = _plan()
    config = _config()
    seal = _seal(plan, config)
    path = tmp_path / "plan" / "plan_state.json"

    state = publish_initial_plan_state(path, plan, seal, config)

    assert path.read_bytes() == canonical_json_bytes(state)
    with pytest.raises(FileExistsError):
        publish_initial_plan_state(path, plan, seal, config)


@pytest.mark.parametrize(
    ("status", "changes"),
    [
        ("pending", {"attempts": 1}),
        ("pending", {"notes": "unexpected"}),
        ("in_progress", {"attempts": 1, "last_error": "old error"}),
        ("done", {"attempts": 1, "commit_sha": None}),
        ("blocked", {"attempts": 4, "last_error": None}),
        ("blocked_by_dependency", {"attempts": 0, "notes": "unexpected"}),
    ],
)
def test_schema_locks_status_field_condition_table(
    status: str,
    changes: dict[str, Any],
) -> None:
    plan, config, seal, state = _state()
    task = state["tasks"][0]
    task["status"] = status
    task.update(changes)

    report = plan_state_snapshot_lint(plan, state, seal, config)

    assert "PLAN-STATE-SCHEMA" in report.error_codes()


def test_done_requires_bound_commit_and_attempt_specific_evidence() -> None:
    plan, config, seal, state = _state()
    task = state["tasks"][0]
    task.update(
        {
            "status": "done",
            "attempts": 2,
            "notes": "Implemented.",
            "commit_sha": "a" * 40,
            "acceptance_evidence": {
                "task_evidence_ref": {
                    "path": "test_results/task_evidence/T-001/attempt_002.json",
                    "sha256": "bc" * 32,
                }
            },
        }
    )
    assert plan_state_snapshot_lint(plan, state, seal, config).ok

    task["acceptance_evidence"]["task_evidence_ref"]["path"] = (
        "test_results/task_evidence/T-001/attempt_001.json"
    )
    report = plan_state_snapshot_lint(plan, state, seal, config)
    assert "PLAN-STATE-EVIDENCE-PATH" in report.error_codes()


def test_snapshot_lint_locks_attempt_limit_and_blocked_exhaustion() -> None:
    plan, config, seal, state = _state()
    task = state["tasks"][0]
    task.update(
        {
            "status": "blocked",
            "attempts": 3,
            "last_error": "T1 attempt failed.",
        }
    )

    report = plan_state_snapshot_lint(plan, state, seal, config)

    assert "PLAN-STATE-BLOCKED-ATTEMPTS" in report.error_codes()

    task["attempts"] = 5
    report = plan_state_snapshot_lint(plan, state, seal, config)
    assert "PLAN-STATE-ATTEMPT-LIMIT" in report.error_codes()


def test_snapshot_lint_rejects_task_set_and_independent_anchor_drift() -> None:
    plan, config, seal, state = _state()
    state["tasks"].pop()
    seal["config_snapshot_sha256"] = "00" * 32
    seal["plan"]["sha256"] = "11" * 32

    report = plan_state_snapshot_lint(plan, state, seal, config)

    assert {
        "PLAN-STATE-TASK-SET",
        "PLAN-STATE-CONFIG-HASH",
        "PLAN-STATE-PLAN-HASH",
        "PLAN-STATE-PLAN-REF",
    } <= report.error_codes()


def test_initializer_rejects_duplicate_or_invalid_plan_tasks() -> None:
    plan = _plan()
    plan["tasks"][1]["id"] = "T-001"
    config = _config()
    seal = _seal(plan, config)

    with pytest.raises(PlanStateValidationError, match="DUP-TASK"):
        initialize_plan_state(plan, seal, config)


def test_snapshot_does_not_mutate_inputs() -> None:
    plan, config, seal, state = _state()
    before = copy.deepcopy((plan, config, seal, state))

    plan_state_snapshot_lint(plan, state, seal, config)

    assert (plan, config, seal, state) == before


def _published_state(
    tmp_path: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = _plan()
    config = _config()
    seal = _seal(plan, config)
    path = tmp_path / "plan" / "plan_state.json"
    publish_initial_plan_state(path, plan, seal, config)
    return path, plan, config, seal


def _transition(
    path: Path,
    task_id: str,
    event: object,
    plan: dict[str, Any],
    config: dict[str, Any],
    seal: dict[str, Any],
) -> dict[str, Any]:
    return transition_plan_state(
        path,
        task_id,
        event,  # type: ignore[arg-type]
        plan=plan,
        s4_seal=seal,
        config_snapshot=config,
    )


def test_attempt_start_retry_and_success_are_deterministic(tmp_path: Path) -> None:
    path, plan, config, seal = _published_state(tmp_path)
    state = _transition(path, "T-001", AttemptStartedEvent(), plan, config, seal)
    task = state["tasks"][0]
    assert (task["status"], task["attempts"], task["last_error"]) == (
        "in_progress",
        1,
        None,
    )

    state = _transition(
        path,
        "T-001",
        AttemptStartedEvent(previous_error="first attempt failed"),
        plan,
        config,
        seal,
    )
    assert state["tasks"][0]["attempts"] == 2
    assert state["tasks"][0]["last_error"] is None

    evidence = {
        "path": "test_results/task_evidence/T-001/attempt_002.json",
        "sha256": "ab" * 32,
    }
    state = _transition(
        path,
        "T-001",
        AttemptSucceededEvent("a" * 40, evidence, notes="done"),
        plan,
        config,
        seal,
    )
    task = state["tasks"][0]
    assert task["status"] == "done"
    assert task["commit_sha"] == "a" * 40
    assert task["acceptance_evidence"]["task_evidence_ref"] == evidence

    with pytest.raises(PlanStateValidationError, match="终态"):
        _transition(path, "T-001", AttemptStartedEvent(), plan, config, seal)


def test_attempts_exhausted_only_blocks_at_total_limit(tmp_path: Path) -> None:
    path, plan, config, seal = _published_state(tmp_path)
    _transition(path, "T-001", AttemptStartedEvent(), plan, config, seal)
    with pytest.raises(PlanStateValidationError, match="attempts=4"):
        _transition(
            path,
            "T-001",
            AttemptsExhaustedEvent("failed early"),
            plan,
            config,
            seal,
        )
    for attempt in (2, 3, 4):
        _transition(
            path,
            "T-001",
            AttemptStartedEvent(previous_error=f"attempt {attempt - 1} failed"),
            plan,
            config,
            seal,
        )
    state = _transition(
        path,
        "T-001",
        AttemptsExhaustedEvent("T1 attempt failed", notes="blocked"),
        plan,
        config,
        seal,
    )
    assert state["tasks"][0]["status"] == "blocked"
    assert state["tasks"][0]["attempts"] == 4


def test_dependency_blocked_is_proved_from_plan_and_full_state(tmp_path: Path) -> None:
    path, plan, config, seal = _published_state(tmp_path)
    with pytest.raises(PlanStateValidationError, match="没有 blocked 依赖"):
        _transition(path, "T-002", DependencyBlockedEvent(), plan, config, seal)

    _transition(path, "T-001", AttemptStartedEvent(), plan, config, seal)
    for attempt in (2, 3, 4):
        _transition(
            path,
            "T-001",
            AttemptStartedEvent(previous_error=f"attempt {attempt - 1} failed"),
            plan,
            config,
            seal,
        )
    _transition(
        path,
        "T-001",
        AttemptsExhaustedEvent("exhausted"),
        plan,
        config,
        seal,
    )
    state = _transition(
        path,
        "T-002",
        DependencyBlockedEvent(),
        plan,
        config,
        seal,
    )
    task = state["tasks"][1]
    assert task["status"] == "blocked_by_dependency"
    assert task["attempts"] == 0
    assert task["last_error"] == "blocked by dependency: T-001"


def test_reconciled_commit_requires_matching_typed_proof(tmp_path: Path) -> None:
    path, plan, config, seal = _published_state(tmp_path)
    _transition(path, "T-001", AttemptStartedEvent(), plan, config, seal)
    ref = {
        "path": "test_results/task_evidence/T-001/attempt_001.json",
        "sha256": "cd" * 32,
    }
    bad = ReconciliationProof._from_verified(
        task_id="T-002",
        attempt=1,
        commit_sha="b" * 40,
        task_evidence_ref=ref,
    )
    with pytest.raises(PlanStateValidationError, match="proof"):
        _transition(
            path,
            "T-001",
            ReconciledCommitEvent(bad),
            plan,
            config,
            seal,
        )

    proof = ReconciliationProof._from_verified(
        task_id="T-001",
        attempt=1,
        commit_sha="b" * 40,
        task_evidence_ref=ref,
    )
    state = _transition(
        path,
        "T-001",
        ReconciledCommitEvent(proof, notes="forward reconciled"),
        plan,
        config,
        seal,
    )
    assert state["tasks"][0]["status"] == "done"
    assert state["tasks"][0]["notes"] == "forward reconciled"


def test_reconciliation_proof_rejects_direct_construction() -> None:
    with pytest.raises(PlanStateValidationError, match="execution reconciliation"):
        ReconciliationProof(
            "T-001",
            1,
            "b" * 40,
            {
                "path": "test_results/task_evidence/T-001/attempt_001.json",
                "sha256": "cd" * 32,
            },
            _token=object(),
        )


def test_pure_transition_validator_rejects_caller_invented_changes() -> None:
    plan, config, _seal_value, state = _state()
    old = state["tasks"][0]
    invented = copy.deepcopy(old)
    invented.update({"status": "in_progress", "attempts": 1, "notes": "injected"})

    with pytest.raises(PlanStateValidationError, match="确定性推导"):
        validate_state_transition(
            old,
            invented,
            AttemptStartedEvent(),
            plan=plan,
            state=state,
            total_limit=config["budgets"]["task_fix_attempts"] + 1,
        )
