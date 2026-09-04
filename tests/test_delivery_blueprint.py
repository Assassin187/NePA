import copy
import json
from pathlib import Path

import pytest

from nepa.speclib.delivery import DeliveryConstraintError, canonical_delivery_blueprint, compile_delivery_blueprint, compile_delivery_constraints
from nepa.speclib.planning import prepare_architecture_inputs


ROOT = Path(__file__).parents[1]


def _constraints(path: str = "tests/fixtures/non_mqtt_application") -> dict:
    prepared = prepare_architecture_inputs(
        ROOT / path / "spec.json",
        ROOT / path / "target.json",
        ROOT / path / "test_bundle.json",
    )
    return compile_delivery_constraints(prepared.spec, prepared.target_profile)


def _architecture(rows: list[tuple[str, str, str, str | None, str, str]]) -> tuple[dict, list[dict]]:
    files = []
    task_files = []
    for slot_id, render_rule, file_class, contract_id, build_role, path in rows:
        is_pattern = "{" in path
        files.append({
            "slot_id": slot_id,
            "path": None if is_pattern else path,
            "path_pattern": path if is_pattern else None,
            "expand_over": "messages" if "{message_id}" in path else ("types" if "{type_id}" in path else None),
            "class": file_class,
            "render_rule": render_rule,
            "owner_module": "module",
            "contract_id": contract_id,
            "build_role": build_role,
            "purpose": slot_id,
        })
        if file_class == "s6_owned":
            task_files.append(path)
    architecture = {
        "modules": [{"id": "module"}],
        "contracts": [{"id": "interface"}],
        "layout": {
            "roots": {"include": "include", "source": "src", "app": "apps", "build": "build"},
            "files": files,
            "build_graph": {"artifacts": [{"artifact_id": "application", "output_path": "build/application", "entry_file_slot": next(slot_id for slot_id, _render_rule, _class, _contract, role, _path in rows if role == "entry_point"), "link_source_slots": [slot_id for slot_id, _render_rule, _class, _contract, role, _path in rows if role == "link_source"]}]},
        },
    }
    tasks = [{"id": "T-001", "deliverable_files": task_files}]
    return architecture, tasks


LEGAL_ROWS = [
    ("header", "s5_frozen", "interface", "none", "header"),
    ("source_stub", "s6_owned", "interface", "none", "header"),
    ("source_stub", "s6_owned", None, "link_source", "source"),
    ("source_stub", "s6_owned", None, "entry_point", "app"),
    ("build_file", "s5_frozen", None, "none", "build"),
    ("doc", "s5_frozen", None, "none", "documentation"),
    ("mechanical", "s5_frozen", "types", "none", "header"),
    ("mechanical", "s5_frozen", None, "link_source", "source"),
]


def test_file_rule_derivation_table_rows_are_literal_and_bijective():
    constraints = _constraints()
    constraints["mechanical_generation_contracts"] = [constraints["mechanical_generation_contracts"][0]]
    rows = [(f"slot-{index}", render_rule, file_class, contract_id, build_role, f"files/{index}.c") for index, (render_rule, file_class, contract_id, build_role, _kind) in enumerate(LEGAL_ROWS)]
    rows[0] = ("header", "header", "s5_frozen", "interface", "none", "files/header.h")
    rows[1] = ("contract-stub", "source_stub", "s6_owned", "interface", "none", "files/stub.h")
    rows[2] = ("source", "source_stub", "s6_owned", None, "link_source", "files/source.c")
    rows[3] = ("entry", "source_stub", "s6_owned", None, "entry_point", "files/entry.c")
    rows[4] = ("build", "build_file", "s5_frozen", None, "none", "files/build")
    rows[5] = ("doc", "doc", "s5_frozen", None, "none", "files/readme.md")
    rows[6] = ("mechanical-header", "mechanical", "s5_frozen", "types", "none", "files/types.h")
    rows[7] = ("mechanical-source", "mechanical", "s5_frozen", None, "link_source", "files/generated.c")
    architecture, tasks = _architecture(rows)
    blueprint = compile_delivery_blueprint(constraints, architecture, tasks=tasks)
    actual = {item["id"]: (item["kind"], item["producer"]) for item in blueprint["file_rules"]}
    expected = {rows[index][0]: (LEGAL_ROWS[index][4], "layout_template" if LEGAL_ROWS[index][1] == "s5_frozen" and LEGAL_ROWS[index][0] not in {"mechanical"} else ("s6_task" if LEGAL_ROWS[index][1] == "s6_owned" else "mechanical_spec")) for index in range(8)}
    assert actual == expected
    assert len(blueprint["file_rules"]) == len(rows)


@pytest.mark.parametrize("row_index,mutate", [
    (0, lambda item: item.update(contract_id=None)),
    (1, lambda item: item.update(build_role="link_source")),
    (2, lambda item: item.update(contract_id="interface")),
    (3, lambda item: item.update(render_rule="mechanical", class_="s5_frozen")),
    (4, lambda item: item.update(build_role="link_source")),
    (5, lambda item: item.update(contract_id="interface")),
    (6, lambda item: item.update(build_role="entry_point")),
    (7, lambda item: item.update(build_role="entry_point")),
])
def test_table_external_tuple_is_rejected(row_index, mutate):
    constraints = _constraints()
    constraints["mechanical_generation_contracts"] = [constraints["mechanical_generation_contracts"][0]]
    rows = [
        ("header", "header", "s5_frozen", "interface", "none", "files/header.h"),
        ("contract-stub", "source_stub", "s6_owned", "interface", "none", "files/stub.h"),
        ("source", "source_stub", "s6_owned", None, "link_source", "files/source.c"),
        ("entry", "source_stub", "s6_owned", None, "entry_point", "files/entry.c"),
        ("build", "build_file", "s5_frozen", None, "none", "files/build"),
        ("doc", "doc", "s5_frozen", None, "none", "files/readme.md"),
        ("mechanical-header", "mechanical", "s5_frozen", "types", "none", "files/types.h"),
        ("mechanical-source", "mechanical", "s5_frozen", None, "link_source", "files/generated.c"),
    ]
    architecture, tasks = _architecture(rows)
    item = architecture["layout"]["files"][row_index]
    mutate(item)
    item.pop("class_", None)
    with pytest.raises(DeliveryConstraintError) as exc:
        compile_delivery_blueprint(constraints, architecture, tasks=tasks)
    assert exc.value.code == "BLUEPRINT_FILE_DERIVATION_INVALID"


def test_message_and_type_expansion_are_target_selected_and_utf8_ordered():
    constraints = _constraints()
    architecture, tasks = _architecture([
        ("messages", "source_stub", "s6_owned", None, "link_source", "src/message_{message_id}.c"),
        ("types", "source_stub", "s6_owned", None, "link_source", "src/type_{type_id}.c"),
        ("entry", "source_stub", "s6_owned", None, "entry_point", "apps/main.c"),
    ])
    tasks[0]["deliverable_files"] = ["src/message_heartbeat_frame.c", "src/type_frame_count.c", "apps/main.c"]
    blueprint = compile_delivery_blueprint(constraints, architecture, tasks=tasks)
    rules = {item["id"]: item for item in blueprint["file_rules"]}
    assert rules["messages"]["expansion"] == "per_message"
    assert rules["types"]["expansion"] == "per_type"
    assert canonical_delivery_blueprint(blueprint) == canonical_delivery_blueprint(copy.deepcopy(blueprint))


def test_expansion_safety_and_ownership_fail_without_partial_output():
    constraints = _constraints()
    architecture, tasks = _architecture([
        ("source", "source_stub", "s6_owned", None, "link_source", "../src/{type_id}.c"),
        ("entry", "source_stub", "s6_owned", None, "entry_point", "apps/main.c"),
    ])
    tasks[0]["deliverable_files"] = ["../src/frame_count.c", "apps/main.c"]
    with pytest.raises(DeliveryConstraintError) as exc:
        compile_delivery_blueprint(constraints, architecture, tasks=tasks)
    assert exc.value.code == "BLUEPRINT_PATH_UNSAFE"
