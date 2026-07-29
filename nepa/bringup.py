"""M1-4a Architecture bring-up 的候选工件指纹。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from nepa.canonical import canonical_sha256


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def architecture_candidate_fingerprint(
    *,
    planning_index: dict[str, Any],
    delivery_constraints: dict[str, Any],
    provider: str,
    requested_model: str,
    config_snapshot_sha256: str,
    response_models: list[str] | None = None,
) -> dict[str, Any]:
    """记录 spike 的稳定输入及 provider 实际模型锚；批准前不得称 frozen。"""
    root = Path(__file__).resolve().parent
    prompt = root / "agents" / "prompts" / "architecture_planner.md"
    schema = root / "schemas" / "architecture-draft.schema.json"
    validator = root / "architecture.py"
    return {
        "schema_version": "1.0",
        "status": "candidate",
        "planning_index_sha256": canonical_sha256(planning_index),
        "delivery_constraints_sha256": canonical_sha256(delivery_constraints),
        "prompt_sha256": _sha256_file(prompt),
        "schema_sha256": _sha256_file(schema),
        "validator_sha256": _sha256_file(validator),
        "provider": provider,
        "requested_model": requested_model,
        "response_models": sorted(set(response_models or [])),
        "model_reconciliation": (
            "pending"
            if not response_models
            else (
                "exact"
                if sorted(set(response_models)) == [requested_model]
                else "mismatch"
            )
        ),
        "config_snapshot_sha256": config_snapshot_sha256,
    }
