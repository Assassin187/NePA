"""Read protocol constants from the gold Spec IR (M0-6/A7 requirement)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

GOLD_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = GOLD_ROOT / "spec" / "spec.json"


@cache
def load_spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def message(name: str) -> dict[str, Any]:
    wanted = name.casefold()
    for item in load_spec()["messages"]:
        if item["name"].casefold() == wanted or item["id"].casefold() == wanted:
            return item
    raise KeyError(name)


def packet_type(name: str) -> int:
    return int(message(name)["packet_type_code"])


def fixed_flags(name: str) -> int:
    packet_type_field = next(f for f in message(name)["fields"] if f["name"] == "packet_type")
    flags = 0
    for bit in packet_type_field["bits"]:
        if bit["name"] == "type":
            continue
        value = int(bit.get("constraint", {}).get("const", 0))
        flags |= value << int(bit["offset"])
    return flags


def fixed_byte(name: str) -> int:
    return (packet_type(name) << 4) | fixed_flags(name)


def field_const(message_name: str, field_name: str) -> Any:
    field = next(f for f in message(message_name)["fields"] if f["name"] == field_name)
    return field["constraint"]["const"]


def bit_const(message_name: str, field_name: str, bit_name: str) -> int:
    field = next(f for f in message(message_name)["fields"] if f["name"] == field_name)
    bit = next(item for item in field["bits"] if item["name"] == bit_name)
    return int(bit["constraint"]["const"])


def named_constant(name: str) -> Any:
    return next(item["value"] for item in load_spec()["constants"] if item["name"] == name)


def varint_max() -> int:
    item = next(t for t in load_spec()["types"] if t["id"] == "mqtt_varint")
    return int(next(c["max"] for c in item["constraints"] if "max" in c))


def default_port() -> int:
    return int(load_spec()["transport"]["default_port"])
