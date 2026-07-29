from __future__ import annotations

import json
from pathlib import Path

from nepa.architecture_spike import aggregate_trials, reconcile_spike_evidence
from nepa.canonical import atomic_write_canonical_json
from nepa.config import PricingEntry


def _trial(first: bool, repaired: bool, failed_gates: set[str]) -> dict:
    gates = {
        f"arch_{index:02d}": f"arch_{index:02d}" not in failed_gates
        for index in range(1, 13)
    }
    return {
        "schema_first_pass": True,
        "schema_candidate_available": True,
        "arch_raw_first_pass": first,
        "arch_semantic_first_pass": first,
        "arch_pass_with_one_repair": first or repaired,
        "first_gate_results": gates,
        "calls": [
            {
                "tokens_in": 10,
                "tokens_out": 5,
                "cost_usd": 0.1,
                "latency_ms": 20,
                "finish_reason": "stop",
            }
        ],
    }


def test_aggregate_uses_all_trials_as_rate_denominator() -> None:
    report = aggregate_trials(
        [
            _trial(True, False, set()),
            _trial(False, True, {"arch_06", "arch_07"}),
        ]
    )
    assert report["trial_count"] == 2
    assert report["arch_semantic_first_pass_rate"] == 0.5
    assert report["arch_pass_with_one_repair_rate"] == 1.0
    assert report["gate_pass_counts"]["arch_06"] == 1
    assert report["failure_cooccurrence"] == {"arch_06+arch_07": 1}
    assert report["usage"]["logical_call_count"] == 2
    assert report["usage"]["cost_usd"] == 0.2


def test_reconcile_reprices_by_response_model_and_records_model_mismatch(
    tmp_path: Path,
) -> None:
    batch_dir = tmp_path / "batch"
    validation_path = batch_dir / "trials" / "trial_001" / "validation.json"
    validation_path.parent.mkdir(parents=True)
    (batch_dir / "artifacts").mkdir()
    trial = _trial(True, False, set())
    trial["calls"][0].update(
        {
            "model": "actual-model",
            "tokens_in": 1_000_000,
            "tokens_out": 500_000,
            "cost_usd": 0.0,
        }
    )
    fingerprint = {
        "schema_version": "1.0",
        "status": "candidate",
        "model": "requested-model",
    }
    atomic_write_canonical_json(validation_path, trial)
    atomic_write_canonical_json(
        batch_dir / "batch.json",
        {
            "schema_version": "1.0",
            "batch_id": "batch",
            "status": "complete",
            "trial_count": 1,
            "completed_trials": 1,
            "fingerprint": fingerprint,
        },
    )
    atomic_write_canonical_json(
        batch_dir / "artifacts" / "candidate_fingerprint.json",
        fingerprint,
    )

    report = reconcile_spike_evidence(
        batch_dir,
        {"actual-model": PricingEntry(input=1.0, output=2.0)},
    )

    assert report["usage"]["cost_usd"] == 2.0
    assert report["fingerprint"]["requested_model"] == "requested-model"
    assert report["fingerprint"]["response_models"] == ["actual-model"]
    assert report["fingerprint"]["response_model_pricing_usd_per_mtok"] == {
        "actual-model": {"input": 1.0, "output": 2.0}
    }
    assert report["fingerprint"]["model_reconciliation"] == "mismatch"
    persisted = json.loads(validation_path.read_text(encoding="utf-8"))
    assert persisted["calls"][0]["cost_usd"] == 2.0
