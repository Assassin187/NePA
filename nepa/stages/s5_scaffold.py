"""S5 deterministic scaffold controller (design 6.5)."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from nepa.assets import AssetValidationError, validate_profile, validate_test_bundle
from nepa.canonical import atomic_write_canonical_json, canonical_sha256
from nepa.delivery import compile_delivery_blueprint, compile_delivery_constraints
from nepa.round_store import RoundStore
from nepa.run_store import RunStore
from nepa.scaffold import (
    materialize_language_build_file,
    materialize_mechanical_files,
    materialize_stubs,
    materialize_target_templates,
)
from nepa.speclib.lint import plan_full_lint
from nepa.test_summary import build_test_summary
from nepa.tools.build import BuildResults, BuildTool
from nepa.tools.fs_ops import sha256_file
from nepa.tools.git_ops import GitOps

__all__ = ["S5Error", "S5Inputs", "S5Result", "scaffold_project"]

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
GateRunner = Callable[[Path, tuple[dict[str, Any], ...]], list[dict[str, Any]]]


class S5Error(RuntimeError):
    """S5 controlled failure with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class S5Inputs:
    spec: dict[str, Any]
    target: dict[str, Any]
    language: dict[str, Any]
    test_bundle: dict[str, Any]
    manifest: dict[str, Any]
    input_refs: dict[str, dict[str, str]]
    repo_root: Path


@dataclass(frozen=True, slots=True)
class S5Result:
    artifact_manifest: dict[str, Any]
    contract_map: dict[str, Any]
    summary: dict[str, Any]
    workspace_head: str
    published: bool


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    )


def _validate(value: dict[str, Any], schema_name: str) -> None:
    errors = sorted(
        _validator(schema_name).iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, item.absolute_path)) or '<root>'}: {item.message}"
            for item in errors[:6]
        )
        raise S5Error("SCAFFOLD_ARTIFACT_INVALID", f"{schema_name}: {detail}")


def _load_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise S5Error("SCAFFOLD_RECEIPT_INVALID", f"{description}: {exc}") from exc
    if not isinstance(value, dict):
        raise S5Error("SCAFFOLD_RECEIPT_INVALID", f"{description} root must be object")
    return value


def _verify_inputs(store: RunStore, inputs: S5Inputs) -> None:
    expected_values = {
        "spec": inputs.spec,
        "target_profile": inputs.target,
        "language_profile": inputs.language,
        "test_bundle": inputs.test_bundle,
    }
    if set(inputs.input_refs) != set(expected_values):
        raise S5Error("SCAFFOLD_INPUTS_INVALID", "input_refs must contain four inputs")
    run_inputs = store.read()["inputs"]
    for kind, value in expected_values.items():
        ref = inputs.input_refs[kind]
        path = store.run_dir / ref["path"]
        if not path.is_file() or sha256_file(path) != ref["sha256"]:
            raise S5Error("SCAFFOLD_INPUTS_INVALID", f"{kind} frozen file drift")
        loaded = _load_object(path, f"frozen {kind}")
        if canonical_sha256(loaded) != canonical_sha256(value):
            raise S5Error("SCAFFOLD_INPUTS_INVALID", f"{kind} in-memory value drift")
        run_key = kind
        if run_inputs.get(run_key) != ref and {
            key: run_inputs.get(run_key, {}).get(key) for key in ("path", "sha256")
        } != ref:
            raise S5Error("SCAFFOLD_INPUTS_INVALID", f"{kind} run receipt drift")
    manifest_ref = inputs.test_bundle.get("manifest_ref", {})
    if canonical_sha256(inputs.manifest) != manifest_ref.get("sha256"):
        raise S5Error("SCAFFOLD_INPUTS_INVALID", "manifest canonical hash drift")
    try:
        validate_profile(
            inputs.target,
            kind="target",
            workspace_root=inputs.repo_root,
        )
        validate_profile(
            inputs.language,
            kind="language",
            workspace_root=inputs.repo_root,
        )
        validate_test_bundle(inputs.test_bundle, workspace_root=inputs.repo_root)
    except AssetValidationError as exc:
        raise S5Error("SCAFFOLD_INPUTS_INVALID", str(exc)) from exc


def _artifact_manifest(
    *,
    constraints: dict[str, Any],
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    slots = {item["path"]: item for item in constraints["file_slots"]}
    files: list[dict[str, Any]] = []
    for index, item in enumerate(blueprint["files"], start=1):
        slot = slots[item["path"]]
        files.append(
            {
                "id": f"file-{index:03d}",
                "rule_id": slot["rule_id"],
                "path": item["path"],
                "kind": slot["kind"],
                "created_by_stage": "s5",
                "mutability": item["mutability"],
                "owner_task_id": item["owner_task_id"],
                "producer": item["producer"],
            }
        )
    variants = sorted(str(item["id"]) for item in constraints["build_variants"])
    build_artifacts = []
    for item in constraints["build_artifacts"]:
        build_artifacts.append(
            {
                key: deepcopy(item[key])
                for key in (
                    "id",
                    "deliverable_id",
                    "kind",
                    "path",
                    "link_source_set_id",
                    "source_paths",
                )
            }
            | {"build_variant_ids": variants}
        )
    value = {
        "schema_version": "1.0",
        "delivery_blueprint_sha256": blueprint["content_sha256"],
        "files": files,
        "build_artifacts": build_artifacts,
    }
    _validate(value, "artifact-manifest.schema.json")
    return value


def _contract_map(
    *,
    plan: dict[str, Any],
    target: dict[str, Any],
    constraints: dict[str, Any],
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    external = {item["id"]: item for item in target["external_contracts"]}
    artifact_ids_by_contract: dict[str, list[str]] = {}
    deliverables = {item["id"]: item for item in target["deliverables"]}
    for artifact in constraints["build_artifacts"]:
        deliverable = deliverables[artifact["deliverable_id"]]
        for contract_id in deliverable["entry_contract_ids"]:
            artifact_ids_by_contract.setdefault(contract_id, []).append(artifact["id"])
    all_artifacts = sorted(str(item["id"]) for item in constraints["build_artifacts"])
    contracts: list[dict[str, Any]] = []
    for contract in sorted(plan["architecture"]["contracts"], key=lambda item: item["id"]):
        contract_id = str(contract["id"])
        profile = external.get(contract_id)
        entrypoints = deepcopy(profile["entrypoints"]) if profile is not None else []
        artifact_ids = sorted(artifact_ids_by_contract.get(contract_id, []))
        if profile is not None and any(
            item.get("kind") == "build" for item in entrypoints
        ):
            artifact_ids = all_artifacts
        contracts.append(
            {
                "id": contract_id,
                "kind": contract["kind"],
                "owner": contract["owner"],
                "ready_gate": contract["ready_gate"],
                "provider_task_id": contract.get("provider_task_id"),
                "interface_files": deepcopy(contract["interface_files"]),
                "entrypoints": entrypoints,
                "build_artifact_ids": artifact_ids,
            }
        )
    required = {
        str(contract_id)
        for test in constraints["tests"]
        for contract_id in test["required_contracts"]
    }
    mapped = {item["id"] for item in contracts}
    if not required <= mapped:
        raise S5Error(
            "SCAFFOLD_CONTRACT_UNRESOLVED",
            f"manifest contracts are not mapped: {sorted(required - mapped)}",
        )
    value = {
        "schema_version": "1.0",
        "delivery_blueprint_sha256": blueprint["content_sha256"],
        "contracts": contracts,
    }
    _validate(value, "contract-map.schema.json")
    return value


def _build_result(value: BuildResults) -> list[dict[str, Any]]:
    results = []
    for variant_id, result in (
        ("release", value.release),
        ("san", value.sanitizer),
    ):
        if result is None:
            continue
        passed = result.code == 0 and not result.timed_out
        item: dict[str, Any] = {
            "variant_id": variant_id,
            "result": "pass" if passed else ("error" if result.timed_out else "fail"),
            "duration_ms": result.duration_ms,
            "warnings": 0,
            "errors": 0 if passed else 1,
        }
        if not passed:
            excerpt = (result.stderr or result.stdout or "build failed")[-4000:]
            item["output_excerpt"] = excerpt
        results.append(item)
    return results


def _receipt_ref(store: RunStore, relative: str) -> dict[str, str]:
    path = store.run_dir / relative
    return {"path": relative, "sha256": sha256_file(path)}


def _verify_done(store: RunStore) -> S5Result:
    refs = store.meta.stages["s5"].output_refs or {}
    values: dict[str, dict[str, Any]] = {}
    for key, schema_name in (
        ("artifact_manifest", "artifact-manifest.schema.json"),
        ("contract_map", "contract-map.schema.json"),
        ("s5_summary", "test-summary.schema.json"),
    ):
        ref = refs.get(key)
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise S5Error("SCAFFOLD_RECEIPT_INVALID", f"missing S5 {key} receipt")
        path = store.run_dir / ref["path"]
        if not path.is_file() or sha256_file(path) != ref.get("sha256"):
            raise S5Error("SCAFFOLD_RECEIPT_INVALID", f"invalid S5 {key} receipt")
        value = _load_object(path, key)
        _validate(value, schema_name)
        values[key] = value
    head = refs.get("workspace_head")
    git = GitOps(store.run_dir / "workspace")
    if not isinstance(head, str) or not git.is_ancestor(head, git.head()) or not git.is_clean():
        raise S5Error("SCAFFOLD_RECEIPT_INVALID", "workspace no longer matches S5 receipt")
    return S5Result(
        artifact_manifest=values["artifact_manifest"],
        contract_map=values["contract_map"],
        summary=values["s5_summary"],
        workspace_head=head,
        published=False,
    )


def _prepare_workspace(workspace: Path) -> None:
    entries = list(workspace.iterdir())
    if not entries:
        return
    git = GitOps(workspace)
    if (workspace / ".git").is_dir() and git.has_commit() and git.is_clean():
        return
    quarantine_root = workspace.parent / "cache"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    index = 1
    while (quarantine_root / f"s5-workspace-{index:03d}").exists():
        index += 1
    destination = quarantine_root / f"s5-workspace-{index:03d}"
    os.replace(workspace, destination)
    workspace.mkdir()


def _materialize_all(
    workspace: Path,
    *,
    inputs: S5Inputs,
    constraints: dict[str, Any],
) -> None:
    materialize_target_templates(
        workspace,
        workspace_root=inputs.repo_root,
        target=inputs.target,
        constraints=constraints,
    )
    materialize_mechanical_files(
        workspace,
        workspace_root=inputs.repo_root,
        spec=inputs.spec,
        target=inputs.target,
        language=inputs.language,
        constraints=constraints,
    )
    materialize_stubs(workspace, constraints=constraints)
    materialize_language_build_file(
        workspace,
        workspace_root=inputs.repo_root,
        language=inputs.language,
        constraints=constraints,
    )


def _workspace_matches_expected(
    workspace: Path,
    *,
    inputs: S5Inputs,
    constraints: dict[str, Any],
) -> bool:
    cache_root = workspace.parent / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="s5-expected-", dir=cache_root) as temp:
        expected = Path(temp)
        _materialize_all(expected, inputs=inputs, constraints=constraints)
        expected_files = {
            path.relative_to(expected).as_posix(): path
            for path in expected.rglob("*")
            if path.is_file()
        }
        actual_files = {
            path.relative_to(workspace).as_posix(): path
            for path in workspace.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(workspace).parts
        }
        if set(expected_files) != set(actual_files):
            return False
        return all(
            expected_files[relative].read_bytes() == actual_files[relative].read_bytes()
            for relative in expected_files
        )


def scaffold_project(
    store: RunStore,
    inputs: S5Inputs,
    *,
    build_tool: BuildTool,
    gate_runner: GateRunner,
) -> S5Result:
    """Execute S5 through artifact publication, build/test evidence, and receipt."""
    if store.meta.stages["s5"].status == "done":
        return _verify_done(store)
    store.begin_stage("s5")
    _verify_inputs(store, inputs)
    s4_refs = store.meta.stages["s4"].output_refs or {}
    plan_ref = s4_refs.get("plan")
    if not isinstance(plan_ref, dict):
        raise S5Error("SCAFFOLD_PLAN_INVALID", "missing S4 plan receipt")
    plan_path = store.run_dir / str(plan_ref.get("path", "plan/plan.json"))
    plan = _load_object(plan_path, "sealed plan")
    if canonical_sha256(plan) != plan_ref.get("sha256"):
        raise S5Error("SCAFFOLD_PLAN_INVALID", "sealed Plan hash drift")
    constraints = compile_delivery_constraints(
        inputs.spec,
        inputs.target,
        inputs.language,
        inputs.test_bundle,
        inputs.manifest,
    )
    blueprint = compile_delivery_blueprint(
        constraints,
        plan["architecture"],
        plan["work_packages"],
        plan["tasks"],
    )
    if (
        blueprint["content_sha256"] != plan.get("delivery_blueprint_sha256")
        or blueprint["content_sha256"] != s4_refs.get("delivery_blueprint_sha256")
    ):
        raise S5Error("DELIVERY_BLUEPRINT_DRIFT", "S5 recomputed blueprint differs")
    report = plan_full_lint(
        plan,
        inputs.spec,
        constraints=constraints,
        blueprint=blueprint,
        tests_manifest=inputs.manifest["tests"],
        config_snapshot=store.meta.config_snapshot,
        expected_input_refs=inputs.input_refs,
    )
    if not report.ok:
        raise S5Error("SCAFFOLD_PLAN_LINT_FAILED", str(sorted(report.error_codes())))

    workspace = store.run_dir / "workspace"
    _prepare_workspace(workspace)
    git = GitOps(workspace)
    if not git.has_commit():
        _materialize_all(workspace, inputs=inputs, constraints=constraints)
        workspace_head = git.init_and_commit()
    else:
        if git.commit_count() != 1 or not _workspace_matches_expected(
            workspace,
            inputs=inputs,
            constraints=constraints,
        ):
            raise S5Error(
                "SCAFFOLD_WORKSPACE_INVALID",
                "existing S5 commit does not match deterministic scaffold tree",
            )
        workspace_head = git.head()

    artifact_manifest = _artifact_manifest(
        constraints=constraints,
        blueprint=blueprint,
    )
    contract_map = _contract_map(
        plan=plan,
        target=inputs.target,
        constraints=constraints,
        blueprint=blueprint,
    )
    artifact_path = store.run_dir / "plan" / "artifact_manifest.json"
    contract_path = store.run_dir / "plan" / "contract_map.json"
    atomic_write_canonical_json(artifact_path, artifact_manifest)
    atomic_write_canonical_json(contract_path, contract_map)

    build = build_tool.both(workspace)
    build_results = _build_result(build)
    if not build.ok:
        raise S5Error("SCAFFOLD_BUILD_FAILED", "default build variants failed")
    s5_tests = tuple(
        item
        for item in inputs.manifest["tests"]
        if item["gate"] == "s5"
        and any(
            row["nodeid"] == item["nodeid"] and row["enabled"]
            for row in plan["coverage"]["tests"]
        )
    )
    cases = gate_runner(workspace, s5_tests)
    if any(item.get("result") != "pass" for item in cases):
        raise S5Error("SCAFFOLD_TEST_FAILED", "one or more gate=s5 tests failed")
    clean = build_tool.sandbox.exec(["make", "clean"], str(workspace), timeout_s=60)
    if clean.code != 0 or not git.is_clean():
        raise S5Error("SCAFFOLD_WORKSPACE_DIRTY", "workspace is not clean after S5")

    rounds = RoundStore(store.run_dir)
    index = rounds.load_index()
    round_id = len(index["rounds"]) + 1
    parent_round_id = index["rounds"][-1]["round_id"] if index["rounds"] else None
    summary = build_test_summary(
        round_id=round_id,
        trigger="s5_scaffold",
        workspace_head=workspace_head,
        workspace_tree=git.commit_tree(workspace_head),
        parent_round_id=parent_round_id,
        plan_sha256=canonical_sha256(plan),
        delivery_blueprint_sha256=blueprint["content_sha256"],
        manifest_sha256=inputs.test_bundle["manifest_ref"]["sha256"],
        bundle_tree_sha256=inputs.test_bundle["bundle_tree_sha256"],
        build_results=build_results,
        cases=cases,
    )
    entry = rounds.publish_round(
        summary,
        stage="s5",
        producer_context={},
    )
    summary_ref = entry["summary_ref"]
    store.set_stage_status(
        "s5",
        "done",
        output_refs={
            "artifact_manifest": _receipt_ref(store, "plan/artifact_manifest.json"),
            "contract_map": _receipt_ref(store, "plan/contract_map.json"),
            "s5_summary": summary_ref,
            "workspace_head": workspace_head,
        },
    )
    return S5Result(
        artifact_manifest=artifact_manifest,
        contract_map=contract_map,
        summary=summary,
        workspace_head=workspace_head,
        published=True,
    )
