"""Protocol-neutral deterministic serial task compiler, Plan 6.0."""
from __future__ import annotations
from typing import Any
from .lint import digest, lint_spec, lint_target
from .planning import message_context, referenced_requirements


def compile_plan(spec: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    for report in (lint_spec(spec), lint_target(target, spec)):
        if not report["valid"]:
            raise ValueError(report["errors"])
    tasks: list[dict[str, Any]] = []
    def add(task_id: str, kind: str, goal: str, context: dict[str, Any], primary: list[str] | None = None) -> None:
        tasks.append({"id": task_id, "kind": kind, "goal": goal, "context": context,
                      "requirement_ids": primary or [], "depends_on": tasks[-1]["id"] if tasks else None})
    add("bootstrap", "bootstrap",
        "BOOTSTRAP ONLY: create the project, Makefile, README and a server startup/shutdown path honoring Target. "
        "Build both variants. Do not implement message codecs or all protocol behaviors here: dedicated later tasks "
        "will implement them. A listening process with clean shutdown is sufficient for THIS task, not final success. "
        "Use the provided transport/target facts; avoid reading the entire protocol before creating initial files. "
        "No protocol implementation dependency or canned project. All interfaces remain editable by later tasks.",
        {"protocol": spec["protocol"], "transport": spec.get("transport"),
         "requirements": referenced_requirements(spec, [spec.get("transport", {})])})
    add("shared-wire", "wire", "Implement the common wire types, buffers and transport foundations from the full original facts. "
        "Do not prematurely discard information needed by specified error responses.",
        {"transport": spec.get("transport"), "types": spec["types"],
         "requirements": referenced_requirements(spec, [spec.get("transport", {}), *spec["types"]])})
    roles = set(target["roles"])
    for message in spec["messages"]:
        directions = []
        if roles.intersection(message["receivers"]):
            directions.append("decode")
        if roles.intersection(message["senders"]):
            directions.append("encode")
        add(f"message:{message['id']}", "message",
            f"Implement {', '.join(directions) or 'target-scope review'} for this message using current project interfaces. "
            "Use full fields and referenced requirements; compile affected callers. Behavior-specific error replies "
            "must remain possible. Do not silently turn every constraint violation into a dropped message.",
            {**message_context(spec, message), "directions": directions})
    primary = {}
    for start in range(0, len(spec["requirements"]), 12):
        batch = spec["requirements"][start:start + 12]
        task_id = f"requirements:{start // 12 + 1:03d}"
        ids = [r["id"] for r in batch]
        add(task_id, "requirements", "Implement and integrate every primary requirement, including definitions, using the "
            "current real source. Read other messages/requirements when needed. Connect behavior to the actual runtime path. "
            "Do not limit implementation to minimum acceptance checks. Report exactly these primary requirements at finish.",
            {"requirements": batch}, ids)
        primary.update({req: task_id for req in ids})
    add("final-integration", "integration", "Integrate the entire project, review requirement claims and runtime dispatch. "
        "Use real builds and independent acceptance feedback to fix all mandatory checks. Ensure standalone README, "
        "clean builds and orderly shutdown. Do not omit previously assigned requirements.",
        {"requirement_count": len(spec["requirements"])})
    return {"schema_version": "6.0", "revision": 1, "reason": "deterministic input compilation",
            "input_hashes": {"spec": digest(spec), "target": digest(target)}, "tasks": tasks, "primary_tasks": primary}


def validate_claims(task: dict[str, Any], claims: list[dict[str, Any]], workspace: Any) -> None:
    from pathlib import Path
    from .lint import safe_relative
    expected = task["requirement_ids"]
    actual = [c.get("id") for c in claims]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("finish must report exactly the primary requirements, without duplicates")
    for claim in claims:
        if claim.get("status") not in {"implemented", "already_present", "not_applicable"}:
            raise ValueError("unsupported/deferred requirements cannot pass")
        if not isinstance(claim.get("reason"), str) or not claim["reason"].strip():
            raise ValueError("requirement claim needs an explanation")
        if claim["status"] == "not_applicable":
            if not claim.get("source_refs"):
                raise ValueError("not_applicable needs original requirement/target references")
        else:
            if not claim.get("code_refs"):
                raise ValueError("implementation claims need code locations")
            for ref in claim["code_refs"]:
                path = Path(workspace) / safe_relative(ref.split(":")[0])
                if not path.is_file() or not path.resolve().is_relative_to(Path(workspace).resolve()):
                    raise ValueError(f"code reference does not exist in project: {ref}")
