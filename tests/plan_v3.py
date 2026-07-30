"""Plan v3 共享测试夹具：用真实 Linker 从示例资产构造合法 Plan（5.2、6.4.5）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nepa.config import NepaConfig
from nepa.delivery import compile_delivery_constraints
from nepa.plan_draft import LinkResult, PlanDraftIR, link_plan_draft, normalize_layered_draft

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "nepa" / "schemas" / "examples"

INPUT_REFS: dict[str, dict[str, str]] = {
    "spec": {"path": "spec/spec.json", "sha256": "ab" * 32},
    "target_profile": {"path": "inputs/target.json", "sha256": "cd" * 32},
    "language_profile": {"path": "inputs/language.json", "sha256": "ef" * 32},
    "test_bundle": {"path": "inputs/test_bundle.json", "sha256": "01" * 32},
}


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def make_spec() -> dict[str, Any]:
    """与 target profile 示例的 message 展开一致的最小 Spec IR。"""
    return {
        "schema_version": "3.0",
        "protocol": {"name": "Sample", "version": "1", "roles": ["client", "server"]},
        "transport": {
            "name": "TCP",
            "default_port": 1883,
            "byte_order": "big_endian",
            "req_ids": ["REQ-TRANSPORT-001"],
        },
        "types": [],
        "messages": [
            {
                "id": "connect",
                "name": "CONNECT",
                "senders": ["client"],
                "receivers": ["server"],
                "wire_layout": ["fixed_header"],
                "fields": [
                    {
                        "name": "remaining_length",
                        "loc": "fixed_header",
                        "type": "uint8",
                        "req_ids": ["REQ-FRAME-001"],
                    }
                ],
                "req_ids": ["REQ-CONNECT-001"],
            }
        ],
        "requirements": [
            {
                "id": "REQ-TRANSPORT-001",
                "text": "TCP port 1883 is registered for the sample protocol.",
                "level": "DEFINITION",
                "source_ref": {"section": "1", "quote": "port 1883"},
            },
            {
                "id": "REQ-FRAME-001",
                "text": "Frames must round-trip through the codec.",
                "level": "MUST",
                "source_ref": {"section": "2", "quote": "frame"},
            },
            {
                "id": "REQ-PUBLISH-001",
                "text": "The client must publish one message.",
                "level": "MUST",
                "source_ref": {"section": "3", "quote": "publish"},
            },
            {
                "id": "REQ-CONNECT-001",
                "text": "The server must accept a connection.",
                "level": "MUST",
                "source_ref": {"section": "4", "quote": "connect"},
            },
        ],
    }


def make_manifest_tests() -> list[dict[str, Any]]:
    """覆盖 s5/task/s7_only 三种 gate 与一条默认禁用的 l3 测试。"""
    return [
        {
            "nodeid": "tests/l0_build/test_scaffold.py::test_builds",
            "description": "scaffold quick check",
            "layer": "l0",
            "req_ids": ["REQ-TRANSPORT-001"],
            "gate": "s5",
            "required_contracts": ["build-system"],
        },
        {
            "nodeid": "tests/l1_codec/test_frame.py::test_roundtrip",
            "description": "codec round-trip",
            "layer": "l1",
            "req_ids": ["REQ-FRAME-001"],
            "gate": "task",
            "required_contracts": ["codec-cli"],
            "build_variant_ids": ["san"],
        },
        {
            "nodeid": "tests/l2_behavior/test_connect.py::test_accepts",
            "description": "server accepts one connection",
            "layer": "l2",
            "req_ids": ["REQ-CONNECT-001"],
            "gate": "task",
            "required_contracts": ["server-process"],
        },
        {
            "nodeid": "tests/l2_behavior/test_publish.py::test_publishes",
            "description": "client publishes one message",
            "layer": "l2",
            "req_ids": ["REQ-PUBLISH-001"],
            "gate": "task",
            "required_contracts": ["client-cli"],
        },
        {
            "nodeid": "tests/l3_interop/test_reference.py::test_interop",
            "description": "reference interop",
            "layer": "l3",
            "req_ids": ["REQ-CONNECT-001"],
            "gate": "s7_only",
            "required_contracts": ["server-process"],
        },
    ]


def make_manifest() -> dict[str, Any]:
    return {"schema_version": "2.0", "tests": make_manifest_tests()}


def make_constraints() -> dict[str, Any]:
    return compile_delivery_constraints(
        make_spec(),
        example("target-profile.json"),
        example("language-profile.json"),
        example("test-bundle.json"),
        make_manifest(),
    )


def make_config_snapshot() -> dict[str, Any]:
    return NepaConfig().config_snapshot()


def _shard(work_package_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {"work_package_id": work_package_id, "tasks": [task]}


def make_shards() -> list[dict[str, Any]]:
    """每个工作包一个 shard，文件与 contract 集合恰好闭合。"""
    return [
        _shard(
            "wp-codec",
            {
                "local_id": "codec",
                "title": "Message codec",
                "goal": "Round-trip the selected message through the public codec.",
                "kind": "codec",
                "instructions": "Implement encode/decode and expose the codec CLI.",
                "deliverable_files": ["src/codec/codec_connect.c", "apps/codec_cli.c"],
                "context_refs": [{"kind": "message", "id": "connect"}],
                "requirement_responsibilities": [
                    {"req_id": "REQ-FRAME-001", "role": "primary"}
                ],
                "provides_contracts": ["codec-cli"],
                "consumes_contracts": [],
                "depends_on": [],
                "acceptance": {"outcome": "The codec CLI round-trips the message."},
            },
        ),
        _shard(
            "wp-client",
            {
                "local_id": "app",
                "title": "Client application",
                "goal": "Publish one message through the client CLI.",
                "kind": "app",
                "instructions": "Compose the codec CLI into one client operation.",
                "deliverable_files": ["apps/client_app.c"],
                "context_refs": [],
                "requirement_responsibilities": [
                    {"req_id": "REQ-PUBLISH-001", "role": "primary"}
                ],
                "provides_contracts": ["client-cli"],
                "consumes_contracts": ["codec-cli"],
                "depends_on": [],
                "acceptance": {"outcome": "The client publishes one message."},
            },
        ),
        _shard(
            "wp-transport",
            {
                "local_id": "transport",
                "title": "Connection transport",
                "goal": "Provide bounded connection lifecycle handling.",
                "kind": "transport",
                "instructions": "Implement the internal transport interface and byte movement.",
                "deliverable_files": ["include/proto/core_transport.h", "src/net.c"],
                "context_refs": [
                    {"kind": "interface_file", "id": "include/proto/core_transport.h"}
                ],
                "requirement_responsibilities": [],
                "provides_contracts": ["core-transport"],
                "consumes_contracts": [],
                "depends_on": [],
                "acceptance": {"outcome": "The transport exposes the frozen interface."},
            },
        ),
        _shard(
            "wp-server",
            {
                "local_id": "app",
                "title": "Server composition",
                "goal": "Accept one connection through the server process.",
                "kind": "app",
                "instructions": "Compose codec and transport into the server process.",
                "deliverable_files": ["apps/server_app.c"],
                "context_refs": [],
                "requirement_responsibilities": [
                    {"req_id": "REQ-CONNECT-001", "role": "primary"}
                ],
                "provides_contracts": ["server-process"],
                "consumes_contracts": ["codec-cli", "core-transport"],
                "depends_on": [],
                "acceptance": {"outcome": "The server accepts one connection."},
            },
        ),
    ]


def make_draft() -> PlanDraftIR:
    return normalize_layered_draft(example("architecture-draft.json"), make_shards())


def make_link_result() -> LinkResult:
    """跑真实 Linker，返回 candidate Plan、Blueprint 与 link report。"""
    return link_plan_draft(
        make_draft(),
        spec=make_spec(),
        manifest=make_manifest(),
        constraints=make_constraints(),
        input_refs=INPUT_REFS,
        config_snapshot=make_config_snapshot(),
    )


def make_plan() -> dict[str, Any]:
    return make_link_result().plan
