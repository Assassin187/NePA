from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from nepa.delivery import build_planning_index, compile_delivery_constraints
from nepa.profile_build import build_default_assets

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = ROOT / "profiles" / "templates" / "mqtt-client-broker"
SESSION_HEADER = TEMPLATE_ROOT / "include" / "mqtt" / "mqtt_session.h"
NET_HEADER = TEMPLATE_ROOT / "include" / "mqtt" / "mqtt_net.h"


def _macro(source: str, name: str) -> int:
    match = re.search(rf"^#define {name} ([0-9]+)u$", source, re.MULTILINE)
    assert match is not None, name
    return int(match.group(1))


def test_o18_header_constants_match_frozen_target_profile() -> None:
    target_path, _, _ = build_default_assets(ROOT)
    target = json.loads(target_path.read_text(encoding="utf-8"))
    limits = {item["id"]: item["maximum"] for item in target["resource_limits"]}
    header = SESSION_HEADER.read_text(encoding="utf-8")

    assert _macro(header, "MQTT_MAX_CONNECTIONS") == limits["connection-capacity"] == 16
    assert _macro(header, "MQTT_OUT_BATCH_MAX_ITEMS") == limits[
        "fanout-target-capacity"
    ] == 16
    assert _macro(header, "MQTT_OUT_ITEM_MAX_BYTES") == limits[
        "out-item-byte-capacity"
    ] == 4096
    assert _macro(header, "MQTT_OUT_BATCH_MAX_BYTES") == limits[
        "out-batch-byte-capacity"
    ] == 65536
    assert all(
        item["exhaustion_behavior"] == "resource_error"
        for item in target["resource_limits"]
    )


def test_o18_broker_abi_has_stable_identity_atomic_batch_and_all_events() -> None:
    session = SESSION_HEADER.read_text(encoding="utf-8")
    net = NET_HEADER.read_text(encoding="utf-8")

    assert "typedef uint32_t mqtt_conn_id_t;" in session
    assert "mqtt_conn_id_t conn_id;" in session
    assert "uint8_t bytes[MQTT_OUT_ITEM_MAX_BYTES];" in session
    assert "uint8_t close;" in session
    for symbol in (
        "mqtt_broker_on_connect",
        "mqtt_broker_on_bytes",
        "mqtt_broker_on_disconnect",
        "mqtt_broker_on_tick",
    ):
        assert symbol in session
    assert "partial fanout may be exposed" in re.sub(r"\s+", " ", session)
    assert "mqtt_net_apply_batch" in net
    assert "topic" not in net.lower()
    assert "subscribe" not in net.lower()


def test_o18_headers_compile_as_warning_clean_c99(tmp_path: Path) -> None:
    source = tmp_path / "abi_check.c"
    source.write_text(
        """
#include "mqtt/mqtt_session.h"
#include "mqtt/mqtt_net.h"

typedef char check_connections[(MQTT_MAX_CONNECTIONS == 16u) ? 1 : -1];
typedef char check_targets[(MQTT_OUT_BATCH_MAX_ITEMS == 16u) ? 1 : -1];
typedef char check_item[(MQTT_OUT_ITEM_MAX_BYTES == 4096u) ? 1 : -1];
typedef char check_batch[(MQTT_OUT_BATCH_MAX_BYTES == 65536u) ? 1 : -1];

static int writer(
    void *context,
    mqtt_conn_id_t conn_id,
    const uint8_t *bytes,
    size_t len,
    uint8_t close
) {
    (void)context;
    (void)conn_id;
    (void)bytes;
    (void)len;
    (void)close;
    return 0;
}

int main(void) {
    mqtt_out_batch_t batch = {0};
    return mqtt_net_apply_batch(&batch, writer, 0);
}
""".lstrip(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "gcc",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsyntax-only",
            "-I",
            str(TEMPLATE_ROOT / "include"),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_default_assets_compile_gold_delivery_constraints_and_safe_s4_view() -> None:
    target_path, language_path, bundle_path = build_default_assets(ROOT)
    target = json.loads(target_path.read_text(encoding="utf-8"))
    language = json.loads(language_path.read_text(encoding="utf-8"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    spec = json.loads(
        (ROOT / "golds" / "mqtt-3.1.1-min" / "spec" / "spec.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        (ROOT / "golds" / "mqtt-3.1.1-min" / "tests_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    constraints = compile_delivery_constraints(
        spec,
        target,
        language,
        bundle,
        manifest,
    )
    index = build_planning_index(
        spec,
        constraints,
        manifest,
        estimated_input_tokens=12000,
        output_tokens_reserved=8000,
        context_limit=32000,
        safety_margin_tokens=4000,
    )

    assert len(constraints["tests"]) == 22
    assert index["preflight"]["fits"] is True
    assert all("layer" not in item for item in index["tests"])
    slots = {item["path"]: item for item in constraints["file_slots"]}
    assert slots["include/mqtt/mqtt_session.h"]["mutability"] == "s5_frozen"
    assert slots["include/mqtt/mqtt_net.h"]["mutability"] == "s5_frozen"
