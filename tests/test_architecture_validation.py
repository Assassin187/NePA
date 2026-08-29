import copy

import pytest

from nepa.speclib.architecture import ArchitectureError
from nepa.speclib.architecture import validate_architecture
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs


def _valid_draft():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    planning = build_planning_index(prepared, manifest, constraints)
    message_paths = [f"src/codec/codec_{message_id}.c" for message_id in constraints["naming"]["message_ids"].values()]
    layout_files = [
        {"slot_id": "build-file", "path": "Makefile", "path_pattern": None, "expand_over": None, "class": "s5_frozen", "render_rule": "build_file", "owner_module": "codec", "contract_id": None, "build_role": "none", "purpose": "Build configuration"},
        {"slot_id": "session-header", "path": "include/core/session.h", "path_pattern": None, "expand_over": None, "class": "s5_frozen", "render_rule": "header", "owner_module": "codec", "contract_id": "session-contract", "build_role": "none", "purpose": "Session interface"},
        {"slot_id": "network-header", "path": "include/core/net.h", "path_pattern": None, "expand_over": None, "class": "s5_frozen", "render_rule": "header", "owner_module": "net", "contract_id": "network-contract", "build_role": "none", "purpose": "Network interface"},
        {"slot_id": "message-codecs", "path": None, "path_pattern": "src/codec/codec_{message_id}.c", "expand_over": "messages", "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None, "build_role": "link_source", "purpose": "Codec implementation"},
        {"slot_id": "session-source", "path": "src/session/session.c", "path_pattern": None, "expand_over": None, "class": "s6_owned", "render_rule": "source_stub", "owner_module": "session", "contract_id": None, "build_role": "link_source", "purpose": "Session implementation"},
        {"slot_id": "network-source", "path": "src/net/net.c", "path_pattern": None, "expand_over": None, "class": "s6_owned", "render_rule": "source_stub", "owner_module": "net", "contract_id": None, "build_role": "link_source", "purpose": "Network implementation"},
        {"slot_id": "application-entry", "path": "apps/server_main.c", "path_pattern": None, "expand_over": None, "class": "s6_owned", "render_rule": "source_stub", "owner_module": "net", "contract_id": None, "build_role": "entry_point", "purpose": "Application entry"},
    ]
    modules = [
        {"id": "codec", "name": "Codec", "purpose": "codec", "responsibilities": ["encode"], "non_goals": ["no io"], "owns_files": message_paths, "provides_contracts": [], "consumes_contracts": ["session-contract"]},
        {"id": "session", "name": "Session", "purpose": "session", "responsibilities": ["session"], "non_goals": ["no net"], "owns_files": ["src/session/session.c"], "provides_contracts": [], "consumes_contracts": []},
        {"id": "net", "name": "Net", "purpose": "net", "responsibilities": ["net"], "non_goals": ["no codec"], "owns_files": ["src/net/net.c", "apps/server_main.c"], "provides_contracts": [], "consumes_contracts": ["session-contract"]},
    ]
    contracts = [
        {"id": "session-contract", "purpose": "session", "owner": "s5", "interface_files": ["include/core/session.h"], "ready_gate": "s5", "provider": "s5", "consumers": ["codec", "net"]},
        {"id": "network-contract", "purpose": "network", "owner": "s5", "interface_files": ["include/core/net.h"], "ready_gate": "s5", "provider": "s5", "consumers": ["session"]},
    ]
    for module in modules:
        module["provides_contracts"] = []
        module["consumes_contracts"] = [contract["id"] for contract in contracts if module["id"] in contract["consumers"]]
    work_packages = [{"id": "wp-" + module["id"], "title": module["name"], "goal": "goal", "module": module["id"], "kind": "implementation", "context_refs": [], "requirement_responsibilities": [], "allowed_files": module["owns_files"], "provides_contracts": [], "consumes_contracts": module["consumes_contracts"], "depends_on": [], "acceptance": {"outcome": "done"}} for module in modules]
    work_packages[1]["requirement_responsibilities"] = [{"req_id": req["id"], "role": "primary"} for req in planning["requirements"] if req["level"] != "DEFINITION"]
    draft = {"schema_version": "2.0", "decisions": [], "assumptions": ["a"], "contracts": contracts, "modules": modules, "work_packages": work_packages, "layout": {"roots": {"include": "include/core", "source": "src", "app": "apps", "build": "."}, "files": layout_files, "build_graph": {"artifacts": [{"artifact_id": "server", "output_path": "build/server", "entry_file_slot": "application-entry", "link_source_slots": ["message-codecs", "session-source", "network-source"]}]}}}
    return draft, planning, manifest, constraints


def test_all_architecture_gates_run_and_repeat():
    draft, planning, manifest, constraints = _valid_draft()
    first = validate_architecture(draft, planning, manifest, constraints)
    second = validate_architecture(draft, planning, manifest, constraints)
    assert first == second
    assert [gate["id"] for gate in first["gates"]] == [f"arch_{index:02d}" for index in range(1, 16)]
    assert first["verdict"] == "pass"


def test_multiple_defects_do_not_short_circuit():
    draft, planning, manifest, constraints = _valid_draft()
    broken = copy.deepcopy(draft)
    broken["modules"][0]["owns_files"].append("unknown.c")
    broken["work_packages"][0]["depends_on"] = ["wp-codec"]
    result = validate_architecture(broken, planning, manifest, constraints)
    assert result["verdict"] == "fail"
    assert len(result["gates"]) == 15


def test_arch_07_requires_exact_module_work_package_contract_union():
    draft, planning, manifest, constraints = _valid_draft()
    broken = copy.deepcopy(draft)
    broken["work_packages"][0]["consumes_contracts"] = []
    result = validate_architecture(broken, planning, manifest, constraints)
    arch_07 = next(gate for gate in result["gates"] if gate["id"] == "arch_07")
    assert arch_07["verdict"] == "fail"
    assert any(issue["path"] == "/modules/0/consumes_contracts" for issue in result["issues"])


@pytest.mark.parametrize("extra_role", ["primary", "supporting"])
def test_arch_10_rejects_duplicate_or_mixed_responsibility(extra_role):
    draft, planning, manifest, constraints = _valid_draft()
    broken = copy.deepcopy(draft)
    existing = broken["work_packages"][1]["requirement_responsibilities"][0]
    broken["work_packages"][1]["requirement_responsibilities"].append({"req_id": existing["req_id"], "role": extra_role})
    result = validate_architecture(broken, planning, manifest, constraints)
    arch_10 = next(gate for gate in result["gates"] if gate["id"] == "arch_10")
    assert arch_10["verdict"] == "fail"
    assert any(issue["code"] == "ARCH_RESPONSIBILITY_INVALID" for issue in result["issues"])


def test_parent_refs_are_checked_against_actual_validator_inputs():
    draft, planning, manifest, constraints = _valid_draft()
    parent_refs = {
        "architecture_draft": {"path": "parents/draft.json", "sha256": "0" * 64},
        "planning_index": {"path": "parents/planning.json", "sha256": "0" * 64},
        "manifest_metadata": {"path": "parents/manifest.json", "sha256": "0" * 64},
        "delivery_constraints": {"path": "parents/constraints.json", "sha256": "0" * 64},
    }
    with pytest.raises(ArchitectureError, match="actual input bytes"):
        validate_architecture(draft, planning, manifest, constraints, parent_refs=parent_refs)


@pytest.mark.parametrize(
    ("gate", "mutate", "code"),
    [
        ("arch_11", lambda draft: draft["layout"]["files"][0].update(path="../unsafe"), "ARCH_LAYOUT_PATH_UNSAFE"),
        ("arch_12", lambda draft: draft["layout"]["files"][4].update(owner_module="codec"), "ARCH_LAYOUT_OWNER_MISMATCH"),
        ("arch_13", lambda draft: draft["layout"]["build_graph"]["artifacts"][0]["link_source_slots"].pop(), "ARCH_LINK_SOURCE_CLOSURE"),
        ("arch_14", lambda draft: draft["contracts"][0].update(provider="net"), "ARCH_LAYER_DIRECTION_INVALID"),
        ("arch_15", lambda draft: draft["layout"]["files"][0].update(path="vendor"), "ARCH_PATH_TOKEN_INVALID"),
    ],
)
def test_each_free_layout_gate_reports_its_local_defect(gate, mutate, code):
    draft, planning, manifest, constraints = _valid_draft()
    mutate(draft)
    result = validate_architecture(draft, planning, manifest, constraints)
    assert next(item for item in result["gates"] if item["id"] == gate)["verdict"] == "fail"
    assert any(item["gate"] == gate and item["code"] == code for item in result["issues"])
