"""Protocol-neutral S6 coding context and full-file response validation."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..schemas import load_example, load_schema
from ..speclib.lint import canonical_json_bytes


CODER_INPUTS = (
    "task", "work_package", "architecture", "spec_slice", "contract_map",
    "interface_files", "language_guidance", "current_files",
)
FIXER_INPUTS = CODER_INPUTS + ("execution_mode", "failed_candidate", "validation_feedback", "diagnosis")


class S6AgentError(ValueError):
    """A coding response or context cannot be admitted to S6."""


def coding_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    return load_schema("coding-response.schema.json"), load_example("coding-response.example.json")


def validate_coding_response(value: Any) -> dict[str, Any]:
    schema, _example = coding_contract()
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: (tuple(error.absolute_path), error.message))
    if errors:
        raise S6AgentError(f"invalid coding response: {errors[0].message}")
    if not isinstance(value, Mapping):
        raise S6AgentError("coding response must be an object")
    files = value["files"]
    paths = [item["path"] for item in files]
    if len(paths) != len(set(paths)):
        raise S6AgentError("coding response contains duplicate file paths")
    if any("plan" in step.lower() and ("amend" in step.lower() or "change" in step.lower()) for step in value["micro_plan"]):
        raise S6AgentError("coding response cannot change the Plan")
    return copy.deepcopy(dict(value))


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def project_s6_context(
    *,
    task: Mapping[str, Any],
    work_package: Mapping[str, Any] | None,
    architecture: Mapping[str, Any],
    spec_slice: Any,
    contract_map: Mapping[str, Any],
    interface_files: Mapping[str, str],
    language_guidance: Mapping[str, Any] | str,
    current_files: Mapping[str, str],
    execution_mode: str = "normal",
    failed_candidate: Any | None = None,
    validation_feedback: Any | None = None,
    diagnosis: Any | None = None,
    max_tokens: int = 24000,
) -> tuple[dict[str, str], dict[str, int]]:
    """Build the exact prompt inputs and deterministic token accounting.

    Required task, architecture, Spec, contract and file content is never
    removed.  The delivery guidance is the only field that may be trimmed.
    """

    required: dict[str, str] = {
        "task": _json(task),
        "work_package": _json(work_package or {}),
        "architecture": _json(architecture),
        "spec_slice": _json(spec_slice),
        "contract_map": _json(contract_map),
        "interface_files": _json({key: interface_files[key] for key in sorted(interface_files, key=lambda item: item.encode("utf-8"))}),
        "language_guidance": _json(language_guidance),
        "current_files": _json({key: current_files[key] for key in sorted(current_files, key=lambda item: item.encode("utf-8"))}),
    }
    if any(not isinstance(value, str) for value in required.values()):
        raise S6AgentError("S6 context values must be serializable")
    is_fixer = execution_mode != "normal" or failed_candidate is not None or validation_feedback is not None or diagnosis is not None
    optional = {
        "execution_mode": execution_mode,
        "failed_candidate": _json(failed_candidate or {}),
        "validation_feedback": _json(validation_feedback or {}),
        "diagnosis": _json(diagnosis or {}),
    } if is_fixer else {}
    # Account for the exact UTF-8 payload.  Four bytes per token is the frozen,
    # conservative estimator used by S6; character counts undercharge non-ASCII
    # context and can make admission depend on the host encoding.
    def tokens(value: str) -> int:
        size = len(value.encode("utf-8"))
        return 0 if size == 0 else max(1, (size + 3) // 4)

    required_tokens = {key: tokens(value) for key, value in required.items() if key != "language_guidance"}
    required_total = sum(required_tokens.values())
    fixed_optional_total = sum(tokens(value) for value in optional.values())
    remaining = max_tokens - required_total - fixed_optional_total
    if remaining < 0:
        raise S6AgentError("required S6 context exceeds coder_context_max_tokens")
    guidance_budget = remaining
    if tokens(required["language_guidance"]) > guidance_budget:
        # Guidance is advisory, but each injected value must remain canonical
        # JSON.  Never byte/character-truncate a JSON document.
        required["language_guidance"] = "{}"
        if tokens(required["language_guidance"]) > guidance_budget:
            raise S6AgentError("required S6 context exceeds coder_context_max_tokens")
    merged = {**required, **optional}
    return merged, {"required_tokens": required_total, "total_tokens": sum(tokens(value) for value in merged.values()), "limit": max_tokens}


def normalize_candidate(value: Any, task: Mapping[str, Any], file_ledger: Mapping[str, Any] | None = None) -> dict[str, bytes]:
    """Admit a response only when every returned path is task-owned."""

    response = validate_coding_response(value)
    allowed = set(task.get("deliverable_files", []))
    frozen = {row.get("path") for row in (file_ledger or {}).get("files", []) if row.get("class") == "s5_frozen"}
    result: dict[str, bytes] = {}
    for item in response["files"]:
        path = item["path"]
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or "\\" in path or not path.strip() or path in frozen:
            raise S6AgentError(f"unsafe or frozen candidate path: {path}")
        if path not in allowed:
            raise S6AgentError(f"candidate path is outside the task whitelist: {path}")
        try:
            content = item["content"].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise S6AgentError(f"candidate content is not UTF-8: {path}") from exc
        result[path] = content
    if not result:
        raise S6AgentError("candidate must contain at least one file")
    return result


def candidate_tree_hash(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.encode("utf-8")):
        raw_path = path.encode("utf-8")
        digest.update(len(raw_path).to_bytes(4, "big"))
        digest.update(raw_path)
        digest.update(len(files[path]).to_bytes(8, "big"))
        digest.update(files[path])
    return digest.hexdigest()


__all__ = ["CODER_INPUTS", "FIXER_INPUTS", "S6AgentError", "candidate_tree_hash", "coding_contract", "normalize_candidate", "project_s6_context", "validate_coding_response"]
