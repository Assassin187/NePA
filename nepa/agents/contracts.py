"""M1 Agent 结构化输出契约（设计文档 6.4、6.6.3、P8）。"""

from __future__ import annotations

from typing import Any

CODER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
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
    "required": ["files", "notes"],
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
