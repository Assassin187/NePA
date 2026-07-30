"""M1 Agent 结构化输出契约（设计文档 6.4、6.6.3、P8）。"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


@cache
def architecture_draft_schema() -> dict[str, Any]:
    """活动 ArchitectureDraft Schema；调用方不得维护第二份内嵌副本。"""
    value = json.loads(
        (_SCHEMA_DIR / "architecture-draft.schema.json").read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise TypeError("architecture-draft schema root must be object")
    return value


def _load_schema(name: str) -> dict[str, Any]:
    value = json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{name} schema root must be object")
    return value


@cache
def task_shard_schema() -> dict[str, Any]:
    """活动 TaskPlanner 局部语义输出契约。"""
    return _load_schema("task-shard.schema.json")


@cache
def plan_critic_schema() -> dict[str, Any]:
    """活动 PlanCritic 结构化 issue-list 输出契约。"""
    return _load_schema("plan-critic.schema.json")


@cache
def flat_plan_draft_schema() -> dict[str, Any]:
    """A9 消融专用 FlatPlanBaseline 的单次完整语义草稿契约（6.4）。"""
    return _load_schema("flat-plan-draft.schema.json")


@cache
def s4_state_schema() -> dict[str, Any]:
    """S4 内部检查点状态契约（5.6.6）；下游不得作为事实源消费。"""
    return _load_schema("s4-state.schema.json")

CODER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "micro_plan": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "files": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["micro_plan", "files", "notes"],
    "additionalProperties": False,
}

DIAGNOSER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "minLength": 1},
        "suspect_files": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "fix_guidance": {"type": "string", "minLength": 1},
    },
    "required": ["root_cause", "suspect_files", "fix_guidance"],
    "additionalProperties": False,
}
