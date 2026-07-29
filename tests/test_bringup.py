from __future__ import annotations

from nepa.bringup import architecture_candidate_fingerprint


def test_candidate_fingerprint_binds_prompt_schema_validator_and_inputs() -> None:
    value = architecture_candidate_fingerprint(
        planning_index={"schema_version": "1.0"},
        delivery_constraints={"schema_version": "1.0"},
        provider="stub",
        requested_model="model",
        config_snapshot_sha256="ab" * 32,
        response_models=["returned-model"],
    )

    assert value["status"] == "candidate"
    assert value["requested_model"] == "model"
    assert value["response_models"] == ["returned-model"]
    assert value["model_reconciliation"] == "mismatch"
    for key in (
        "planning_index_sha256",
        "delivery_constraints_sha256",
        "prompt_sha256",
        "schema_sha256",
        "validator_sha256",
    ):
        assert len(value[key]) == 64
