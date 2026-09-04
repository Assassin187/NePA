from nepa.speclib.plan import build_coverage


def test_coverage_binds_earliest_task_and_preserves_disabled_static_rows():
    spec = {"requirements": [{"id": "REQ-ONE", "level": "MUST"}]}
    manifest = {
        "tests": [
            {"nodeid": "tests/test_one.py::test_behavior", "gate": "task", "layer": "l0", "req_ids": ["REQ-ONE"]},
            {"nodeid": "tests/test_structure.py::test_header", "gate": "s5", "layer": "l1", "req_ids": ["REQ-ONE"]},
        ]
    }
    packages = {"wp-one": {"requirement_responsibilities": [{"req_id": "REQ-ONE", "role": "primary"}]}}
    tasks = [{"id": "T-001", "work_package": "wp-one", "requirement_responsibilities": [{"req_id": "REQ-ONE", "role": "primary"}]}]

    coverage = build_coverage(spec, manifest, packages, tasks, {"T-001": ("wp-one", "T-001")}, set(), {"stages": {"l1": False}})

    assert coverage == {
        "requirements": [{
            "req_id": "REQ-ONE",
            "primary_work_package_id": "wp-one",
            "primary_task_id": "T-001",
            "supporting_task_ids": [],
            "test_nodeids": ["tests/test_one.py::test_behavior", "tests/test_structure.py::test_header"],
        }],
        "tests": [
            {"nodeid": "tests/test_one.py::test_behavior", "gate": "task", "enabled": True, "task_id": "T-001"},
            {"nodeid": "tests/test_structure.py::test_header", "gate": "s5", "enabled": False, "task_id": None},
        ],
    }
