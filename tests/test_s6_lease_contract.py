import hashlib

import pytest

from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan_state import PlanStateError, validate_lease_authorization


def _package():
    plan = {
        "tasks": [
            {"id": "T-001", "task_uid": "a" * 16, "work_package": "wp", "deliverable_files": ["src/a.c"], "consumes_contracts": []},
            {"id": "T-002", "task_uid": "b" * 16, "work_package": "wp", "deliverable_files": ["src/b.c"], "consumes_contracts": []},
        ],
        "architecture": {"contracts": []},
    }
    sha = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": sha, "version": "1.0.0", "revision_seq": 0, "epoch": "E0"}
    state = {"plan_ref": ref, "tasks": [
        {"id": "T-001", "task_uid": "a" * 16, "status": "in_progress", "execution_mode": "normal", "attempts": 1},
        {"id": "T-002", "task_uid": "b" * 16, "status": "done", "execution_mode": "normal", "attempts": 1},
    ], "s6_attempts_used": 1}
    ledger = {"files": [{"path": "src/b.c", "class": "s6_owned", "owner_history": [{"task_uid": "b" * 16}]}]}
    auth = {"schema_version": "1.0", "active_plan_ref": ref, "baseline_commit": "1" * 40, "baseline_tree": "2" * 64, "current_task_id": "T-001", "current_task_uid": "a" * 16, "lenders": [{"task_id": "T-002", "task_uid": "b" * 16, "paths": ["src/b.c"], "evidence_refs": [{"path": "x", "sha256": "3" * 64}]}]}
    config = {"budgets": {"task_fix_attempts": 3, "s6_total_attempts_cap": 8, "s6_lease_limit": 1}}
    return plan, state, ledger, auth, config


@pytest.mark.s6_lease
def test_authorization_is_pure_and_bounded():
    plan, state, ledger, auth, config = _package()
    result = validate_lease_authorization(auth, plan=plan, state=state, file_ledger=ledger, revision_ledger={"entries": []}, config_snapshot=config, baseline_commit="1" * 40, baseline_tree="2" * 64)
    assert result == auth
    assert state["s6_attempts_used"] == 1


@pytest.mark.s6_lease
def test_authorization_rejects_unsafe_or_third_path():
    plan, state, ledger, auth, config = _package()
    auth["lenders"][0]["paths"] = ["../escape"]
    with pytest.raises(PlanStateError):
        validate_lease_authorization(auth, plan=plan, state=state, file_ledger=ledger, revision_ledger={"entries": []}, config_snapshot=config, baseline_commit="1" * 40, baseline_tree="2" * 64)
