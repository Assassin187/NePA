from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from nepa.architecture import arch_validate
from nepa.delivery import (
    DeliveryBlueprintError,
    DeliveryCompileError,
    build_planning_index,
    compile_delivery_blueprint,
    compile_delivery_constraints,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "nepa" / "schemas" / "examples"


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _spec() -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "protocol": {"name": "Sample", "version": "1", "roles": ["client", "server"]},
        "transport": {"kind": "stream"},
        "types": [],
        "messages": [{"id": "connect", "req_ids": ["REQ-CONNECT-001"], "fields": []}],
        "constants": [],
        "requirements": [
            {
                "id": "REQ-FRAME-001",
                "level": "MUST",
                "text": "Frame must round-trip.",
                "source_ref": [{"section": "1", "quote": "frame"}],
            },
            {
                "id": "REQ-PUBLISH-001",
                "level": "MUST",
                "text": "Client must publish.",
                "source_ref": [{"section": "2", "quote": "publish"}],
            },
            {
                "id": "REQ-CONNECT-001",
                "level": "MUST",
                "text": "Server must accept a connection.",
                "source_ref": [{"section": "3", "quote": "connect"}],
            },
        ],
    }


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "tests": [
            {
                "nodeid": "tests/test_sample.py::test_codec",
                "description": "codec",
                "layer": "l1",
                "req_ids": ["REQ-FRAME-001"],
                "gate": "task",
                "required_contracts": ["codec-cli"],
                "build_variant_ids": ["san"],
            }
        ],
    }


def _compiled() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    spec = _spec()
    target = _example("target-profile.json")
    constraints = compile_delivery_constraints(
        spec,
        target,
        _example("language-profile.json"),
        _example("test-bundle.json"),
        _manifest(),
    )
    index = build_planning_index(
        spec,
        constraints,
        _manifest(),
        estimated_input_tokens=1000,
        output_tokens_reserved=2000,
        context_limit=8000,
        safety_margin_tokens=500,
    )
    return target, constraints, index


def test_delivery_constraints_expand_all_file_rules_and_test_defaults() -> None:
    _, constraints, _ = _compiled()
    paths = {item["path"] for item in constraints["file_slots"]}
    assert paths == {
        "Makefile",
        "README.md",
        "include/proto/codec.h",
        "include/proto/core_transport.h",
        "src/codec/codec_connect.c",
        "src/net.c",
        "apps/codec_cli.c",
        "apps/client_app.c",
        "apps/server_app.c",
    }
    assert constraints["tests"][0]["build_variant_ids"] == ["san"]
    assert len(constraints["content_sha256"]) == 64


def test_delivery_constraints_reject_duplicate_expanded_slot() -> None:
    target = _example("target-profile.json")
    duplicate = copy.deepcopy(target["file_rules"][0])
    duplicate["id"] = "duplicate-build"
    target["file_rules"].append(duplicate)
    with pytest.raises(DeliveryCompileError, match="duplicate expanded"):
        compile_delivery_constraints(
            _spec(),
            target,
            _example("language-profile.json"),
            _example("test-bundle.json"),
            _manifest(),
        )


def test_delivery_constraints_compile_explicit_three_segment_build_graph() -> None:
    _, constraints, _ = _compiled()
    artifacts = {item["id"]: item for item in constraints["build_artifacts"]}

    assert artifacts["codec-cli-binary"] == {
        "id": "codec-cli-binary",
        "deliverable_id": "codec-tool",
        "kind": "executable",
        "path": "build/codec_cli",
        "link_source_set_id": "codec-cli-sources",
        "source_rule_ids": ["codec-cli-entry-source", "message-codecs"],
        "source_paths": ["apps/codec_cli.c", "src/codec/codec_connect.c"],
    }


def test_delivery_constraints_reject_dangling_build_graph_reference() -> None:
    target = _example("target-profile.json")
    target["link_source_sets"][0]["file_rule_ids"].append("missing-rule")
    with pytest.raises(DeliveryCompileError, match="unknown file_rule"):
        compile_delivery_constraints(
            _spec(),
            target,
            _example("language-profile.json"),
            _example("test-bundle.json"),
            _manifest(),
        )


def test_delivery_constraints_require_every_app_slot_exactly_once() -> None:
    target = _example("target-profile.json")
    target["link_source_sets"][1]["file_rule_ids"][0] = "message-codecs"
    with pytest.raises(DeliveryCompileError, match="exactly one app slot"):
        compile_delivery_constraints(
            _spec(),
            target,
            _example("language-profile.json"),
            _example("test-bundle.json"),
            _manifest(),
        )


def test_delivery_constraints_require_mechanical_rule_contract_ownership() -> None:
    target = _example("target-profile.json")
    target["file_rules"][2]["producer"] = "mechanical_spec"
    target["file_rules"][2]["template_id"] = "sample-layout"
    target["file_rules"][2]["template_path"] = "codec.h.j2"
    with pytest.raises(DeliveryCompileError, match="every mechanical_spec rule"):
        compile_delivery_constraints(
            _spec(),
            target,
            _example("language-profile.json"),
            _example("test-bundle.json"),
            _manifest(),
        )


def test_planning_index_strips_quotes_and_locks_visibility_and_budget() -> None:
    _, _, index = _compiled()
    assert "quote" not in json.dumps(index)
    assert "layer" not in index["tests"][0]
    assert index["tests"][0] == {
        "nodeid": "tests/test_sample.py::test_codec",
        "description": "codec",
        "req_ids": ["REQ-FRAME-001"],
        "gate": "task",
        "required_contracts": ["codec-cli"],
        "build_variant_ids": ["san"],
    }
    assert index["preflight"]["fits"] is True


def test_arch_validate_accepts_coherent_example() -> None:
    target, constraints, index = _compiled()
    report = arch_validate(
        _example("architecture-draft.json"),
        spec=_spec(),
        target=target,
        constraints=constraints,
        planning_index=index,
    )
    assert report.ok, report.issues
    assert all(report.gate_results.values())


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda draft: draft["architecture"]["contracts"][0].update(
                {"interface_files": ["README.md"]}
            ),
            "ARCH_EXTERNAL_DRIFT",
        ),
        (
            lambda draft: draft["work_packages"][1].update({"depends_on": []}),
            "ARCH_DEPENDENCY_DERIVATION",
        ),
        (
            lambda draft: draft["architecture"]["modules"][0]["owns_files"].append(
                "src/net.c"
            ),
            "ARCH_MODULE_FILE_PARTITION",
        ),
        (
            lambda draft: draft["work_packages"][1][
                "requirement_responsibilities"
            ].clear(),
            "ARCH_REQ_PRIMARY",
        ),
    ],
)
def test_arch_validate_rejects_cross_object_drift(mutation, code: str) -> None:
    target, constraints, index = _compiled()
    draft = _example("architecture-draft.json")
    mutation(draft)
    report = arch_validate(
        draft,
        spec=_spec(),
        target=target,
        constraints=constraints,
        planning_index=index,
    )
    assert not report.ok
    assert code in {issue.code for issue in report.issues}


def test_arch_validate_rejects_preflight_overflow() -> None:
    target, constraints, index = _compiled()
    index["preflight"]["fits"] = False
    report = arch_validate(
        _example("architecture-draft.json"),
        spec=_spec(),
        target=target,
        constraints=constraints,
        planning_index=index,
    )
    assert "ARCH_CONTEXT_TOO_LARGE" in {issue.code for issue in report.issues}


def test_arch_validate_requires_a_common_test_readiness_closure() -> None:
    target, constraints, index = _compiled()
    draft = _example("architecture-draft.json")
    by_id = {item["id"]: item for item in draft["work_packages"]}
    for package_id in ("wp-client", "wp-transport"):
        by_id[package_id]["requirement_responsibilities"].append(
            {"req_id": "REQ-FRAME-001", "role": "supporting"}
        )

    report = arch_validate(
        draft,
        spec=_spec(),
        target=target,
        constraints=constraints,
        planning_index=index,
    )

    assert "ARCH_TEST_READINESS_UNCLOSED" in {
        issue.code for issue in report.issues
    }
    readiness = next(
        issue
        for issue in report.issues
        if issue.code == "ARCH_TEST_READINESS_UNCLOSED"
    )
    assert readiness.gate == "arch_10"
    assert "wp-client" in readiness.message
    assert "wp-transport" in readiness.message


def test_delivery_blueprint_is_deterministic_and_requires_exact_s6_ownership() -> None:
    _, constraints, _ = _compiled()
    draft = _example("architecture-draft.json")
    owned = [slot["path"] for slot in constraints["file_slots"] if slot["mutability"] == "s6_owned"]
    tasks = [
        {"id": f"T-{index:03d}", "deliverable_files": [path]}
        for index, path in enumerate(owned, start=1)
    ]
    blueprint = compile_delivery_blueprint(
        constraints, draft["architecture"], draft["work_packages"], tasks
    )
    assert blueprint["content_sha256"]
    assert {item["owner_task_id"] for item in blueprint["files"] if item["mutability"] == "s6_owned"} == {
        task["id"] for task in tasks
    }
    with pytest.raises(DeliveryBlueprintError, match="no task owner"):
        compile_delivery_blueprint(
            constraints, draft["architecture"], draft["work_packages"], tasks[:-1]
        )
