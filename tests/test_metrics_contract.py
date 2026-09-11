import copy
import json
from pathlib import Path

import pytest

from nepa.metrics import compute_m1_metrics
from nepa.metrics import compute_run_metrics


@pytest.mark.metric_contract
def test_metric_contract_empty_ledger_and_planned_stop_envelopes():
    package = {
        "run": {"termination_kind": "planned_stop"},
        "state": {"tasks": [{"task_uid": "a" * 16, "status": "done"}]},
        "revision_ledger": {"entries": []},
        "s6_receipt": {"s6_build_ok": True, "smoke_pass": True, "artifacts_checked": 1},
    }
    result = compute_m1_metrics(package)
    assert result["task_completion_rate@final"] == {"value": 1.0}
    assert result["revision"]["count_by_level"] == {"value": {"F2": 0, "F3": 0}}
    assert result["lease"]["count"] == {"value": 0}
    assert result["lease"]["success_rate"]["reason"]["code"] == "NO_LEASES"
    assert result["s6_build_ok"] == {"value": True}
    assert result["smoke"]["s6"]["pass"] == {"value": True}
    assert result["build_ok"]["reason"]["code"] == "PLANNED_STOP_NO_TERMINAL_ROUND"


@pytest.mark.metric_contract
def test_metric_contract_lease_nearest_rank_and_input_immutability():
    package = {
        "state": {"tasks": []},
        "revision_ledger": {"entries": [
            {"event_type": "lease_started", "payload": {"lease_id": "lease-1", "leased_paths": ["a"]}},
            {"event_type": "lease_finished", "payload": {"lease_id": "lease-1", "success": True}},
            {"event_type": "lease_started", "payload": {"lease_id": "lease-2", "leased_paths": ["a", "b"]}},
            {"event_type": "lease_finished", "payload": {"lease_id": "lease-2", "success": False}},
        ]},
    }
    before = repr(package)
    result = compute_m1_metrics(package)
    assert result["lease"]["count"] == {"value": 2}
    assert result["lease"]["success_rate"] == {"value": 0.5}
    assert result["lease"]["pending_count"] == {"value": 0}
    assert result["lease"]["external_files_p50"] == {"value": 1}
    assert result["lease"]["external_files_p95"] == {"value": 2}
    assert repr(package) == before


@pytest.mark.metric_contract
def test_metric_contract_split_lineage_preserves_final_r0_and_historical_blocking():
    package = {
        "initial_plan": {"tasks": [
            {"task_uid": "a" * 16}, {"task_uid": "b" * 16},
        ]},
        "state": {"tasks": [
            {"task_uid": "a1" * 8, "status": "done"},
            {"task_uid": "a2" * 8, "status": "blocked"},
            {"task_uid": "b" * 16, "status": "done"},
        ]},
        "lineage_map": {"a" * 16: ["a1" * 8, "a2" * 8], "b" * 16: ["b" * 16]},
        "state_history": [{"tasks": [
            {"task_uid": "a" * 16, "status": "done"},
            {"task_uid": "b" * 16, "status": "done"},
            {"task_uid": "a2" * 8, "status": "blocked"},
        ]}],
        "revision_ledger": {"entries": []},
    }
    result = compute_m1_metrics(package)
    assert result["task_completion_rate@final"] == {"value": 2 / 3}
    assert result["blocked_rate@final"] == {"value": 1 / 3}
    assert result["incomplete_rate@final"] == {"value": 0.0}
    assert result["task_completion_rate@r0"] == {"value": 1 / 2}
    assert result["blocked_rate@r0"] == {"value": 1 / 2}
    assert result["ever_blocked_rate"] == {"value": 1 / 4}


@pytest.mark.metric_contract
def test_metric_contract_regeneration_and_first_pass_denominators_are_distinct():
    package = {
        "state": {"tasks": [
            {"task_uid": "a" * 16, "status": "done"},
            {"task_uid": "b" * 16, "status": "done"},
            {"task_uid": "c" * 16, "status": "blocked"},
            {"task_uid": "c2" * 8, "status": "done"},
        ]},
        "task_evidence": [
            {"task_uid": "a" * 16, "attempt": 1, "accepted": True, "execution_kind": "normal"},
            {"task_uid": "b" * 16, "attempt": 1, "accepted": False, "execution_kind": "normal"},
            {"task_uid": "b" * 16, "attempt": 2, "accepted": True, "execution_kind": "normal"},
            {"task_uid": "c" * 16, "attempt": 1, "accepted": False, "execution_kind": "normal"},
            {"task_uid": "c2" * 8, "attempt": 1, "accepted": True, "execution_kind": "normal"},
        ],
        "regenerated_uids": ["c2" * 8],
        "revision_ledger": {"entries": []},
    }
    result = compute_m1_metrics(package)
    assert result["first_pass_rate"] == {"value": 1 / 3}
    assert result["first_pass_rate_after_revision"] == {"value": 1.0}

    recovered = copy.deepcopy(package)
    recovered["state"]["tasks"][2]["status"] = "done"
    result = compute_m1_metrics(recovered)
    assert result["blocked_rate@final"] == {"value": 0.0}


@pytest.mark.metric_contract
def test_metric_contract_revision_lease_cost_and_receipt_boundaries():
    package = {
        "run": {"termination_kind": "planned_stop"},
        "state": {"tasks": [{"task_uid": "a" * 16, "status": "done"}]},
        "s6_receipt": {"s6_build_ok": True, "smoke_pass": True, "artifacts_checked": 2},
        "s5_receipt": {"status": "pending_repair", "smoke_pass": True, "artifacts_checked": 1, "failures": ["pending_repair"]},
        "revision_ledger": {"entries": [
            {"event_type": "revision_activated", "payload": {"level": "F2", "migration": {"tasks": [
                {"classification": "INHERIT"}, {"classification": "REVALIDATE"},
                {"classification": "AMEND"}, {"classification": "REGENERATE"},
            ]}, "preservation_rate": 0.8, "rework_cost_estimate_usd": 3.0,
                "rework_cost_usd": 1.2, "call_refs": [{"call_id": "c1"}]}},
            {"event_type": "candidate_rejected", "payload": {"failed_gate": "RG-2"}},
            {"event_type": "trigger_evaluated", "payload": {"hit_codes": ["TR-5", "TR-5"]}},
            {"event_type": "revision_evaluated", "payload": {"revision_seq": 1, "resolved": True, "cost_usd": 2.0, "call_refs": [{"call_id": "c1"}]}},
            {"event_type": "revision_evaluated", "payload": {"revision_seq": 1, "resolved": True, "cost_usd": 2.0, "call_refs": [{"call_id": "c1"}]}},
        ]},
        "calls": [{"call_id": "c1", "cost_usd": 2.0}],
    }
    result = compute_m1_metrics(package)
    assert result["revision"]["count_by_level"] == {"value": {"F2": 1, "F3": 0}}
    assert result["revision"]["rejected_by_gate"] == {"value": {"RG-2": 1}}
    assert result["revision"]["trigger_histogram"] == {"value": {"TR-5": 1}}
    assert result["revision"]["migration_mix"] == {"value": {"INHERIT": 1, "REVALIDATE": 1, "AMEND": 1, "REGENERATE": 1}}
    assert result["revision"]["preservation_rate"]["mean"] == {"value": 0.8}
    assert result["revision"]["rework_cost_estimate_usd"] == {"value": 3.0}
    assert result["revision"]["rework_cost_usd"] == {"value": 1.2}
    assert result["revision"]["effectiveness"] == {"value": 0.5}
    assert result["smoke"]["s5"]["pass"] == {"value": False}


@pytest.mark.metric_contract
def test_metric_contract_revision_evaluation_identity_outcomes_and_zero_cost():
    package = {
        "state": {"tasks": []},
        "revision_ledger": {"entries": [
            {"event_type": "revision_evaluated", "payload": {"revision_seq": 1, "resolved": True, "ineffective": False, "cost_usd": 3.0, "call_refs": [{"path": "calls/a.json"}, {"path": "calls/a.json"}]}},
            {"event_type": "revision_evaluated", "payload": {"revision_seq": 2, "resolved": False, "ineffective": True, "cost_usd": 0.0, "call_refs": []}},
            {"event_type": "revision_evaluated", "payload": {"revision_seq": 3, "resolved": False, "ineffective": False, "cost_usd": 0.0, "call_refs": []}},
        ]},
        "calls": [{"output_path": "calls/a.json", "cost_usd": 3.0}],
    }
    result = compute_m1_metrics(package)["revision"]
    assert result["effectiveness"] == {"value": 1 / 3}
    assert result["ineffective_count"] == {"value": 1}

    zero = copy.deepcopy(package)
    zero["revision_ledger"]["entries"] = zero["revision_ledger"]["entries"][1:]
    assert compute_m1_metrics(zero)["revision"]["effectiveness"]["reason"]["code"] == "ZERO_COST_DENOMINATOR"

    without_telemetry = copy.deepcopy(package)
    without_telemetry.pop("calls")
    assert compute_m1_metrics(without_telemetry)["revision"]["effectiveness"] == {"value": 1 / 3}

    conflicting = copy.deepcopy(package)
    conflicting["calls"][0]["cost_usd"] = 4.0
    with pytest.raises(ValueError, match="conflicts with associated call telemetry"):
        compute_m1_metrics(conflicting)

    zero_conflict = copy.deepcopy(package)
    zero_conflict["revision_ledger"]["entries"] = [{
        "event_type": "revision_evaluated",
        "payload": {
            "revision_seq": 1,
            "resolved": True,
            "ineffective": False,
            "cost_usd": 0.0,
            "call_refs": [{"path": "calls/a.json"}],
        },
    }]
    with pytest.raises(ValueError, match="conflicts with associated call telemetry"):
        compute_m1_metrics(zero_conflict)


@pytest.mark.metric_contract
def test_metric_contract_missing_and_empty_inputs_are_explicitly_unavailable():
    result = compute_m1_metrics({"state": {"tasks": []}, "revision_ledger": {"entries": []}})
    assert result["task_completion_rate@final"]["reason"]["code"] == "EMPTY_TASK_SET"
    assert result["revision"]["preservation_rate"]["mean"]["reason"]["code"] == "NO_REVISIONS"
    assert compute_m1_metrics({"state": {"tasks": []}})["lease"]["count"]["reason"]["code"] == "MISSING_REVISION_LEDGER"


@pytest.mark.metric_contract
def test_metric_contract_public_projection_keys_and_read_only_run_adapter(tmp_path: Path):
    package = {"state": {"tasks": []}, "revision_ledger": {"entries": []}}
    result = compute_m1_metrics(package)
    assert set(result) == {
        "task_completion_rate@final", "blocked_rate@final", "incomplete_rate@final",
        "task_completion_rate@r0", "blocked_rate@r0", "incomplete_rate@r0", "ever_blocked_rate",
        "first_pass_rate", "first_pass_rate_after_revision", "s6_build_ok", "build_ok", "smoke",
        "revision", "lease",
    }
    (tmp_path / "plan").mkdir()
    (tmp_path / "run.json").write_text(json.dumps({"termination_kind": "planned_stop"}), encoding="utf-8")
    (tmp_path / "plan" / "plan_state.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    (tmp_path / "plan" / "revision_ledger.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    adapter_result = compute_run_metrics(tmp_path)
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert adapter_result["lease"]["count"] == {"value": 0}
    assert before == after
