"""Packaged production Schema helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parent


def load_schema(name: str) -> dict[str, Any]:
    value = json.loads((_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"schema {name} must be an object")
    return value


def load_example(name: str) -> Any:
    return json.loads((_ROOT / "examples" / name).read_text(encoding="utf-8"))


def architecture_draft_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("architecture-draft.schema.json"), load_example("architecture-draft.example.json")


def architecture_patch_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("architecture-patch.schema.json"), load_example("architecture-patch.example.json")


def task_shard_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("task-shard.schema.json"), load_example("task-shard.example.json")


def s4_commitment_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("s4-commitment.schema.json"), load_example("s4-commitment.example.json")


def s4_checkpoint_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("s4-checkpoint.schema.json"), load_example("s4-checkpoint.example.json")


def s4_state_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("s4-state.schema.json"), load_example("s4-state.example.json")


def plan_critic_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("plan-critic-result.schema.json"), load_example("plan-critic-result.example.json")


def flat_plan_draft_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("flat-plan-draft.schema.json"), load_example("flat-plan-draft.example.json")


__all__ = [
    "architecture_draft_contract",
    "architecture_patch_contract",
    "flat_plan_draft_contract",
    "load_example",
    "load_schema",
    "plan_critic_contract",
    "s4_checkpoint_contract",
    "s4_commitment_contract",
    "s4_state_contract",
    "task_shard_contract",
]
