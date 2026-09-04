import json
from pathlib import Path

import pytest

from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.speclib.plan import PlanError, link_plan, normalize_plan_draft


ROOT = Path(__file__).parents[1]


def _inputs():
    prepared = prepare_architecture_inputs(
        ROOT / "tests/fixtures/non_mqtt_application/spec.json",
        ROOT / "tests/fixtures/non_mqtt_application/target.json",
        ROOT / "tests/fixtures/non_mqtt_application/test_bundle.json",
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    architecture = json.loads((ROOT / "tests/fixtures/non_mqtt_application/architecture-draft.json").read_text(encoding="utf-8"))
    return prepared, constraints, manifest, architecture


def _shards(architecture):
    result = []
    for package in architecture["work_packages"]:
        result.append({
            "schema_version": "1.0",
            "work_package_id": package["id"],
            "tasks": [{
                "local_id": "implement",
                "title": package["title"],
                "goal": package["goal"],
                "kind": "app",
                "instructions": "Implement the declared behavior.",
                "deliverable_files": list(package["allowed_files"]),
                "context_refs": list(package["context_refs"]),
                "requirement_responsibilities": list(package["requirement_responsibilities"]),
                "provides_contracts": list(package["provides_contracts"]),
                "consumes_contracts": list(package["consumes_contracts"]),
                "depends_on": [],
                "acceptance": {"build_variant_ids": ["san"], "tests": []},
            }],
        })
    return result


def test_normalization_rejects_stateful_shards_and_bad_membership():
    _prepared, constraints, _manifest, architecture = _inputs()
    shards = _shards(architecture)
    shards[0]["tasks"][0]["status"] = "pending"
    with pytest.raises(PlanError) as exc:
        normalize_plan_draft(architecture, architecture["work_packages"], shards, constraints=constraints)
    assert exc.value.code == "PLAN_SHARD_SCHEMA_INVALID"

    shards = _shards(architecture)
    shards[0]["tasks"][0]["deliverable_files"] = ["apps/not-owned.c"]
    with pytest.raises(PlanError) as exc:
        normalize_plan_draft(architecture, architecture["work_packages"], shards, constraints=constraints)
    assert exc.value.code == "PLAN_TASK_FILE_PARTITION"


def test_linker_assigns_stable_topology_identifier_order_independent_of_shard_order_and_omits_future_identity_fields():
    prepared, constraints, manifest, architecture = _inputs()
    shards = _shards(architecture)
    first = link_plan(architecture, architecture["work_packages"], shards, constraints, spec=prepared.spec, manifest=manifest)
    second = link_plan(architecture, list(reversed(architecture["work_packages"])), list(reversed(shards)), constraints, spec=prepared.spec, manifest=manifest)
    assert first["plan"] == second["plan"]
    assert first["blueprint"] == second["blueprint"]
    assert first["link_report"] == second["link_report"]
    assert [task["id"] for task in first["plan"]["tasks"]] == ["T-001", "T-002"]
    assert all("task_uid" not in task and "obligation_digest" not in task and "guidance_digest" not in task for task in first["plan"]["tasks"])
    assert first["plan"]["tasks"][1]["context_refs"]
    assert all(task["acceptance"]["tests"] == [] for task in first["plan"]["tasks"])


def test_linker_rejects_dependency_cycles_and_does_not_publish_partial_results():
    prepared, constraints, manifest, architecture = _inputs()
    shards = _shards(architecture)
    shards[0]["tasks"][0]["depends_on"] = ["implement"]
    with pytest.raises(PlanError) as exc:
        link_plan(architecture, architecture["work_packages"], shards, constraints, spec=prepared.spec, manifest=manifest)
    assert exc.value.code == "PLAN_TASK_DAG_CYCLE"


def test_coverage_contains_every_requirement_and_manifest_entry_with_layer_switches():
    prepared, constraints, manifest, architecture = _inputs()
    result = link_plan(
        architecture,
        architecture["work_packages"],
        _shards(architecture),
        constraints,
        spec=prepared.spec,
        manifest=manifest,
        config_snapshot={"stages": {"l1": False}},
    )
    coverage = result["plan"]["coverage"]
    assert {row["req_id"] for row in coverage["requirements"]} == {item["id"] for item in prepared.spec["requirements"]}
    assert {row["nodeid"] for row in coverage["tests"]} == {item["nodeid"] for item in manifest["tests"]}
    assert all(row["enabled"] is False for row in coverage["tests"])
    assert all(task["acceptance"]["tests"] == [] for task in result["plan"]["tasks"])


def test_task_ready_contract_adds_only_the_proven_provider_edge():
    prepared, constraints, manifest, _architecture = _inputs()
    architecture = {
        "schema_version": "2.0",
        "decisions": [],
        "assumptions": ["The codec owns the task-ready API."],
        "contracts": [{"id": "codec-api", "purpose": "codec API", "owner": "codec", "interface_files": ["src/codec/api.h"], "ready_gate": "task", "provider": "codec", "consumers": ["app"]}],
        "modules": [
            {"id": "codec", "name": "Codec", "purpose": "codec", "responsibilities": ["codec"], "non_goals": ["application"], "owns_files": ["src/codec/api.h", "src/codec/impl.c"], "provides_contracts": ["codec-api"], "consumes_contracts": []},
            {"id": "app", "name": "Application", "purpose": "application", "responsibilities": ["application"], "non_goals": ["codec"], "owns_files": ["apps/main.c"], "provides_contracts": [], "consumes_contracts": ["codec-api"]},
        ],
        "work_packages": [
            {"id": "wp-codec", "title": "Codec", "goal": "Implement codec", "module": "codec", "kind": "implementation", "context_refs": [], "requirement_responsibilities": [], "allowed_files": ["src/codec/api.h", "src/codec/impl.c"], "provides_contracts": ["codec-api"], "consumes_contracts": [], "depends_on": [], "acceptance": {"outcome": "Codec is complete."}},
            {"id": "wp-app", "title": "Application", "goal": "Implement application", "module": "app", "kind": "implementation", "context_refs": [], "requirement_responsibilities": [{"req_id": "REQ-ORBIT-001", "role": "primary"}, {"req_id": "REQ-ORBIT-002", "role": "primary"}], "allowed_files": ["apps/main.c"], "provides_contracts": [], "consumes_contracts": ["codec-api"], "depends_on": ["wp-codec"], "acceptance": {"outcome": "Application is complete."}},
        ],
        "layout": {"roots": {"include": "include", "source": "src", "app": "apps", "build": "build"}, "files": [
            {"slot_id": "api", "path": "src/codec/api.h", "path_pattern": None, "expand_over": None, "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": "codec-api", "build_role": "none", "purpose": "Codec interface"},
            {"slot_id": "impl", "path": "src/codec/impl.c", "path_pattern": None, "expand_over": None, "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None, "build_role": "link_source", "purpose": "Codec implementation"},
            {"slot_id": "entry", "path": "apps/main.c", "path_pattern": None, "expand_over": None, "class": "s6_owned", "render_rule": "source_stub", "owner_module": "app", "contract_id": None, "build_role": "entry_point", "purpose": "Application entry"},
        ], "build_graph": {"artifacts": [{"artifact_id": "application", "output_path": "build/application", "entry_file_slot": "entry", "link_source_slots": ["impl"]}]}}
    }
    shards = [
        {"schema_version": "1.0", "work_package_id": "wp-codec", "tasks": [{"local_id": "implement", "title": "Codec", "goal": "Implement codec", "kind": "codec", "instructions": "Implement codec.", "deliverable_files": ["src/codec/api.h", "src/codec/impl.c"], "context_refs": [], "requirement_responsibilities": [], "provides_contracts": ["codec-api"], "consumes_contracts": [], "depends_on": [], "acceptance": {"build_variant_ids": ["san"], "tests": []}}]},
        {"schema_version": "1.0", "work_package_id": "wp-app", "tasks": [{"local_id": "implement", "title": "Application", "goal": "Implement application", "kind": "app", "instructions": "Implement application.", "deliverable_files": ["apps/main.c"], "context_refs": [], "requirement_responsibilities": [{"req_id": "REQ-ORBIT-001", "role": "primary"}, {"req_id": "REQ-ORBIT-002", "role": "primary"}], "provides_contracts": [], "consumes_contracts": ["codec-api"], "depends_on": [], "acceptance": {"build_variant_ids": ["san"], "tests": []}}]},
    ]
    result = link_plan(architecture, architecture["work_packages"], shards, constraints, spec=prepared.spec, manifest=manifest)
    assert result["link_report"]["dependency_edges"] == [{"from": "T-001", "to": "T-002", "reason": "contract"}]
    assert result["plan"]["architecture"]["contracts"][0]["provider_task_id"] == "T-001"
