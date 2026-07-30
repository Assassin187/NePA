"""TaskPlanner 输入裁剪与 S4-G3 shard 门测试（设计 6.4.4）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from nepa.task_shard import (
    build_task_planner_payload,
    frozen_file_paths,
    planning_test_metadata,
    shard_issue_dicts,
    validate_task_shard,
)
from tests.plan_v3 import example, make_constraints, make_manifest, make_shards, make_spec


def _package(work_package_id: str = "wp-codec") -> dict[str, Any]:
    draft = example("architecture-draft.json")
    for item in draft["work_packages"]:
        if item["id"] == work_package_id:
            return deepcopy(item)
    raise AssertionError(work_package_id)


def _shard(work_package_id: str = "wp-codec") -> dict[str, Any]:
    for item in make_shards():
        if item["work_package_id"] == work_package_id:
            return {"schema_version": "1.0", **deepcopy(item)}
    raise AssertionError(work_package_id)


def _codes(shard: Any, *, work_package_id: str = "wp-codec") -> list[str]:
    return [
        issue.code
        for issue in validate_task_shard(
            shard,
            package=_package(work_package_id),
            constraints=make_constraints(),
            max_task_files=4,
        )
    ]


# ---------------------------------------------------------------- payload 裁剪


def _payload(work_package_id: str = "wp-codec") -> dict[str, Any]:
    return build_task_planner_payload(
        _package(work_package_id),
        architecture=example("architecture-draft.json")["architecture"],
        spec=make_spec(),
        manifest=make_manifest(),
        constraints=make_constraints(),
        max_task_files=4,
    )


def test_payload_only_exposes_manifest_metadata_fields() -> None:
    """P1：规划角色只能看到 Test Manifest v2 的白名单元数据。"""
    allowed = {
        "nodeid",
        "description",
        "req_ids",
        "gate",
        "required_contracts",
        "build_variant_ids",
    }
    tests = _payload()["test_metadata"]
    assert tests
    for item in tests:
        assert set(item) <= allowed
        assert "layer" not in item


def test_payload_selects_tests_by_contract_or_requirement_relevance() -> None:
    payload = _payload()
    nodeids = {item["nodeid"] for item in payload["test_metadata"]}
    assert "tests/l1_codec/test_frame.py::test_roundtrip" in nodeids
    # 与本包既无 contract 也无 REQ 交集的 l2 客户端测试不进入输入。
    assert "tests/l2_behavior/test_publish.py::test_publishes" not in nodeids


def test_payload_carries_the_spec_slice_and_frozen_file_boundary() -> None:
    payload = _payload()
    # 包 context_refs 的 message/requirement 与责任 REQ 一起进入切片，不重复。
    assert [item["id"] for item in payload["spec_slice"]["slices"]] == [
        "connect",
        "REQ-FRAME-001",
    ]
    assert {item["id"] for item in payload["spec_slice"]["requirements"]} == {
        "REQ-FRAME-001",
        "REQ-CONNECT-001",
    }
    assert "Makefile" in payload["s5_frozen_files"]
    assert payload["budget"] == {"max_files_per_task": 4}


def test_payload_scopes_architecture_decisions_and_adjacent_contracts() -> None:
    payload = _payload("wp-transport")
    assert [item["id"] for item in payload["architecture_decisions"]] == [
        "separate-core-transport"
    ]
    assert [item["id"] for item in payload["adjacent_contracts"]] == ["core-transport"]
    assert payload["module"]["id"] == "transport"
    assert "owns_files" not in payload["module"]


def test_payload_omits_unrelated_decisions() -> None:
    """context_refs 命中不到本包的决定不进入输入，避免包外实现细节泄漏。"""
    assert _payload("wp-client")["architecture_decisions"] == []


def test_frozen_file_paths_reads_only_s5_frozen_slots() -> None:
    frozen = frozen_file_paths(make_constraints())
    assert "Makefile" in frozen
    assert "apps/codec_cli.c" not in frozen


def test_planning_test_metadata_is_sorted_by_nodeid() -> None:
    selected = planning_test_metadata(
        make_manifest(),
        contract_ids={"codec-cli", "client-cli"},
        req_ids=set(),
    )
    assert [item["nodeid"] for item in selected] == sorted(
        item["nodeid"] for item in selected
    )


# ---------------------------------------------------------------- S4-G3 硬门


def test_valid_shard_passes_every_gate() -> None:
    assert _codes(_shard()) == []


def test_schema_violation_short_circuits_semantic_checks() -> None:
    shard = _shard()
    del shard["tasks"][0]["instructions"]
    assert _codes(shard) == ["SHARD_SCHEMA"]


def test_shard_must_expand_the_requested_work_package() -> None:
    shard = _shard()
    shard["work_package_id"] = "wp-client"
    assert _codes(shard) == ["SHARD_WORK_PACKAGE_ID"]


def test_duplicate_local_ids_are_rejected() -> None:
    shard = _shard()
    second = deepcopy(shard["tasks"][0])
    second["deliverable_files"] = ["apps/codec_cli.c"]
    shard["tasks"][0]["deliverable_files"] = ["src/codec/codec_connect.c"]
    shard["tasks"].append(second)
    assert "SHARD_DUPLICATE_LOCAL_ID" in _codes(shard)


def test_files_outside_allowed_files_are_rejected() -> None:
    shard = _shard()
    shard["tasks"][0]["deliverable_files"] = ["src/net.c", "apps/codec_cli.c"]
    codes = _codes(shard)
    assert "SHARD_FILE_UNKNOWN" in codes
    assert "SHARD_FILE_PARTITION" in codes


def test_writing_an_s5_frozen_file_is_rejected() -> None:
    package = _package()
    package["allowed_files"] = [*package["allowed_files"], "Makefile"]
    shard = _shard()
    shard["tasks"][0]["deliverable_files"] = [*shard["tasks"][0]["deliverable_files"], "Makefile"]
    codes = [
        issue.code
        for issue in validate_task_shard(
            shard,
            package=package,
            constraints=make_constraints(),
            max_task_files=4,
        )
    ]
    assert codes == ["SHARD_FILE_FROZEN"]


def test_two_tasks_cannot_claim_the_same_file() -> None:
    shard = _shard()
    duplicate = deepcopy(shard["tasks"][0])
    duplicate["local_id"] = "codec_cli"
    duplicate["deliverable_files"] = ["apps/codec_cli.c"]
    shard["tasks"].append(duplicate)
    assert "SHARD_FILE_DUPLICATE" in _codes(shard)


def test_incomplete_file_partition_is_reported() -> None:
    shard = _shard()
    shard["tasks"][0]["deliverable_files"] = ["src/codec/codec_connect.c"]
    assert _codes(shard) == ["SHARD_FILE_PARTITION"]


def test_task_file_limit_uses_the_configured_budget() -> None:
    package = _package()
    package["allowed_files"] = [
        "src/codec/codec_connect.c",
        "apps/codec_cli.c",
        "src/net.c",
    ]
    shard = _shard()
    shard["tasks"][0]["deliverable_files"] = list(package["allowed_files"])
    codes = [
        issue.code
        for issue in validate_task_shard(
            shard,
            package=package,
            constraints=make_constraints(),
            max_task_files=2,
        )
    ]
    assert codes == ["SHARD_TASK_FILE_LIMIT"]


def test_contract_sets_must_equal_the_work_package_sets() -> None:
    shard = _shard()
    shard["tasks"][0]["provides_contracts"] = []
    assert "SHARD_CONTRACT_SETS" in _codes(shard)


def test_tasks_cannot_claim_responsibilities_outside_the_package() -> None:
    shard = _shard()
    shard["tasks"][0]["requirement_responsibilities"].append(
        {"req_id": "REQ-PUBLISH-001", "role": "supporting"}
    )
    assert "SHARD_RESPONSIBILITY_OUT_OF_SCOPE" in _codes(shard)


def test_duplicate_responsibility_in_one_task_is_rejected() -> None:
    shard = _shard()
    shard["tasks"][0]["requirement_responsibilities"].append(
        {"req_id": "REQ-FRAME-001", "role": "supporting"}
    )
    assert "SHARD_RESPONSIBILITY_DUPLICATE" in _codes(shard)


def test_primary_responsibility_needs_exactly_one_primary_task() -> None:
    shard = _shard()
    shard["tasks"][0]["requirement_responsibilities"] = [
        {"req_id": "REQ-FRAME-001", "role": "supporting"}
    ]
    assert _codes(shard) == ["SHARD_RESPONSIBILITY_PRIMARY"]


def test_unrefined_package_responsibility_is_reported() -> None:
    shard = _shard()
    shard["tasks"][0]["requirement_responsibilities"] = []
    codes = _codes(shard)
    assert "SHARD_RESPONSIBILITY_UNREFINED" in codes


def test_supporting_package_cannot_declare_a_primary_task() -> None:
    package = _package()
    package["requirement_responsibilities"] = [
        {"req_id": "REQ-FRAME-001", "role": "supporting"}
    ]
    codes = [
        issue.code
        for issue in validate_task_shard(
            _shard(),
            package=package,
            constraints=make_constraints(),
            max_task_files=4,
        )
    ]
    assert codes == ["SHARD_RESPONSIBILITY_PRIMARY"]


def test_local_dependencies_must_stay_inside_the_shard() -> None:
    shard = _shard()
    shard["tasks"][0]["depends_on"] = ["app"]
    assert "SHARD_DEPENDENCY_UNKNOWN" in _codes(shard)


def test_self_dependency_is_rejected() -> None:
    shard = _shard()
    shard["tasks"][0]["depends_on"] = ["codec"]
    codes = _codes(shard)
    assert "SHARD_DEPENDENCY_SELF" in codes


def test_local_dependency_cycle_is_rejected() -> None:
    shard = _shard()
    first = shard["tasks"][0]
    first["deliverable_files"] = ["src/codec/codec_connect.c"]
    first["depends_on"] = ["codec_cli"]
    second = deepcopy(first)
    second["local_id"] = "codec_cli"
    second["deliverable_files"] = ["apps/codec_cli.c"]
    second["depends_on"] = ["codec"]
    second["provides_contracts"] = []
    second["requirement_responsibilities"] = []
    shard["tasks"] = [first, second]
    assert "SHARD_DEPENDENCY_CYCLE" in _codes(shard)


def test_issue_dicts_are_stable_json() -> None:
    shard = _shard()
    shard["tasks"][0]["deliverable_files"] = ["src/codec/codec_connect.c"]
    dicts = shard_issue_dicts(
        validate_task_shard(
            shard,
            package=_package(),
            constraints=make_constraints(),
            max_task_files=4,
        )
    )
    assert dicts == [
        {
            "code": "SHARD_FILE_PARTITION",
            "path": "tasks",
            "message": "allowed_files 未被任务完整覆盖: ['apps/codec_cli.c']",
        }
    ]
