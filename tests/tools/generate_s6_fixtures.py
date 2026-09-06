"""Generate deterministic frozen S6 provider responses from the archived S5 inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from nepa.speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.materialization import derive_rendering_view, render_e0_files
from nepa.speclib.plan import blueprint_task_semantic_projection


ROOT = Path(__file__).resolve().parents[2]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"fixture input is not an object: {path}")
    return value


def _response(files: dict[str, bytes], paths: list[str], notes: str) -> dict[str, Any]:
    return {
        "micro_plan": ["implement the assigned task files"],
        "files": [
            {"path": path, "content": files[path].decode("utf-8") + "/* accepted S6 implementation */\n"}
            for path in paths
        ],
        "notes": notes,
    }


def _failure_response() -> dict[str, Any]:
    return {
        "micro_plan": ["implement the assigned task files"],
        "files": [{"path": "not-owned/by-this-task.c", "content": "int invalid_candidate(void) { return 0; }\n"}],
        "notes": "deliberately rejected frozen-provider candidate",
    }


def _build_case(case_id: str) -> dict[str, Any]:
    source = ROOT / "tests/fixtures/s5" / case_id
    plan = _read(source / "plan.json")
    spec = _read(source / "spec.json")
    target = _read(source / "target.json")
    constraints = _read(source / "constraints.json")
    blueprint = _read(source / "blueprint.json")
    derived_constraints = compile_delivery_constraints(spec, target)
    derived_blueprint = compile_delivery_blueprint(
        derived_constraints,
        plan["architecture"],
        plan["work_packages"],
        blueprint_task_semantic_projection(plan["tasks"]),
    )
    if derived_constraints != constraints or derived_blueprint != blueprint:
        raise ValueError(f"archived S5 fixture does not recompute for {case_id}")
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    rendered = render_e0_files(view, spec, target, blueprint, constraints)
    task_paths = {task["id"]: sorted(task["deliverable_files"]) for task in plan["tasks"]}
    success = {
        task_id: _response(rendered, paths, f"accepted frozen response for {task_id}")
        for task_id, paths in task_paths.items()
    }
    failure = {task_id: _failure_response() for task_id in task_paths}
    ordered_tasks = [task["id"] for task in plan["tasks"]]
    success_sequence = [
        {"task_id": task_id, "attempt": 1, "role": "coder", "tier": "T2", "response": f"success:{task_id}"}
        for task_id in ordered_tasks
    ]
    fix_sequence = [
        {"task_id": task_id, "attempt": 1, "role": "coder", "tier": "T2", "response": f"failure:{task_id}"}
        for task_id in ordered_tasks
    ] + [
        {"task_id": task_id, "attempt": 2, "role": "fixer", "tier": "T2", "response": f"success:{task_id}"}
        for task_id in ordered_tasks
    ]
    four_failure_sequence = [
        {"task_id": task_id, "attempt": attempt, "role": "coder" if attempt == 1 else "fixer", "tier": "T1" if attempt == 4 else "T2", "response": f"failure:{task_id}"}
        for task_id in ordered_tasks
        for attempt in range(1, 5)
    ]
    dependency_tasks = ordered_tasks + ["T-independent"]
    dependency_sequence = [
        {"task_id": ordered_tasks[0], "attempt": 1, "role": "coder", "tier": "T2", "response": f"failure:{ordered_tasks[0]}"},
        {"task_id": "T-independent", "attempt": 1, "role": "coder", "tier": "T2", "response": f"success:{ordered_tasks[0]}"},
    ]
    return {
        "schema_version": "1.0",
        "fixture_id": f"s6-{case_id}",
        "source_fixture": f"tests/fixtures/s5/{case_id}",
        "source_files": {
            path.relative_to(source).as_posix(): _sha256(path.read_bytes())
            for path in sorted(source.rglob("*.json"), key=lambda item: item.relative_to(source).as_posix().encode("utf-8"))
        },
        "rendered_file_hashes": {path: _sha256(data) for path, data in sorted(rendered.items(), key=lambda item: item[0].encode("utf-8"))},
        "responses": {
            **{f"success:{task_id}": value for task_id, value in success.items()},
            **{f"failure:{task_id}": value for task_id, value in failure.items()},
        },
        "sequences": {
            "success": success_sequence,
            "fail_then_fix": fix_sequence,
            "four_failures": four_failure_sequence,
            "dependency_and_independent": {
                "task_graph": {dependency_tasks[0]: [], dependency_tasks[1]: [dependency_tasks[0]], dependency_tasks[2]: []},
                "calls": dependency_sequence,
                "zero_attempt_tasks": [dependency_tasks[1]],
            },
        },
    }


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for case_id in ("mqtt", "non_mqtt"):
        destination = output / case_id / "provider-sequence.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json_bytes(_build_case(case_id)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output.resolve())
