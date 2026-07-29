"""L1 independent byte-level MQTT checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import paho.mqtt.client as paho
import pytest
from harness.mqtt import (
    connect,
    decode_varint,
    encode_varint,
    packet,
    publish,
    subscribe,
    unsubscribe,
)
from harness.spec_model import fixed_byte

pytestmark = [
    pytest.mark.gate("task"),
    pytest.mark.contract("codec-cli"),
    pytest.mark.build_variant("san"),
]


@pytest.mark.req("REQ-FRAME-001")
def test_varint_boundaries() -> None:
    """Remaining Length boundary encodings round-trip."""
    expected = {0: "00", 127: "7f", 128: "8001", 16383: "ff7f", 16384: "808001"}
    for value, encoded_hex in expected.items():
        encoded = encode_varint(value)
        assert encoded.hex() == encoded_hex
        assert decode_varint(encoded) == (value, len(encoded))


@pytest.mark.req("REQ-FRAME-002")
def test_fixed_headers() -> None:
    """All included packet first bytes are derived from the gold spec."""
    expected = {
        "CONNECT": 0x10,
        "CONNACK": 0x20,
        "PUBLISH": 0x30,
        "SUBSCRIBE": 0x82,
        "SUBACK": 0x90,
        "UNSUBSCRIBE": 0xA2,
        "UNSUBACK": 0xB0,
        "PINGREQ": 0xC0,
        "PINGRESP": 0xD0,
        "DISCONNECT": 0xE0,
    }
    assert {name: fixed_byte(name) for name in expected} == expected


@pytest.mark.req("REQ-CONNECT-001")
@pytest.mark.req("REQ-CONNECT-003")
def test_connect_bytes(randomized: dict[str, object]) -> None:
    """Harness CONNECT bytes agree with Paho MQTT v3.1.1 output."""
    client_id = str(randomized["client_a"])
    ours = connect(client_id, keep_alive=30)
    client_options = {
        "client_id": client_id,
        "clean_session": True,
        "protocol": paho.MQTTv311,
    }
    if hasattr(paho, "CallbackAPIVersion"):
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, **client_options)
    else:
        client = paho.Client(**client_options)
    captured: list[bytes] = []
    client._packet_queue = (  # type: ignore[method-assign]
        lambda command, packet_bytes, mid, qos, info=None: (
            captured.append(bytes(packet_bytes)) or paho.MQTT_ERR_SUCCESS
        )
    )
    assert client._send_connect(30) == paho.MQTT_ERR_SUCCESS
    assert captured == [ours]


@pytest.mark.req("REQ-PUBLISH-001")
def test_publish_bytes(randomized: dict[str, object]) -> None:
    """QoS0 publication uses the spec-derived first byte and exact payload."""
    topic = str(randomized["topic"])
    payload = bytes(randomized["payload"])
    encoded = publish(topic, payload)
    assert encoded[0] == fixed_byte("PUBLISH")
    assert encoded.endswith(payload)


@pytest.mark.req("REQ-SUBSCRIBE-001")
def test_subscribe_bytes(randomized: dict[str, object]) -> None:
    """SUBSCRIBE carries packet id, one randomized literal filter, and QoS0."""
    encoded = subscribe(7, str(randomized["topic"]))
    assert encoded[0] == fixed_byte("SUBSCRIBE")
    assert encoded.endswith(b"\x00")


@pytest.mark.req("REQ-UNSUBSCRIBE-001")
def test_unsubscribe_bytes(randomized: dict[str, object]) -> None:
    """UNSUBSCRIBE carries the exact randomized literal filter."""
    encoded = unsubscribe(8, str(randomized["topic"]))
    assert encoded[0] == fixed_byte("UNSUBSCRIBE")
    assert str(randomized["topic"]).encode() in encoded


@pytest.mark.req("REQ-PING-001")
def test_zero_length_packets() -> None:
    """PINGREQ/PINGRESP/DISCONNECT have Remaining Length zero."""
    assert packet("PINGREQ").hex() == "c000"
    assert packet("PINGRESP").hex() == "d000"
    assert packet("DISCONNECT").hex() == "e000"


@pytest.mark.req("REQ-FRAME-001")
@pytest.mark.req("REQ-FRAME-002")
@pytest.mark.req("REQ-CONNECT-001")
@pytest.mark.req("REQ-PUBLISH-001")
def test_workspace_codec_cli_external_contract(
    target: str,
    workspace: Path | None,
    randomized: dict[str, object],
) -> None:
    """Generated codec is exercised only through the frozen 7.4 CLI contract."""
    if target == "reference":
        pytest.skip("reference L1 is cross-validated against Paho instead")
    assert workspace is not None
    executable = workspace / "build" / "mqtt_codec_cli"
    samples = {
        "CONNECT": connect(str(randomized["client_a"])),
        "PUBLISH": publish(str(randomized["topic"]), bytes(randomized["payload"])),
        "SUBSCRIBE": subscribe(7, str(randomized["topic"])),
        "UNSUBSCRIBE": unsubscribe(8, str(randomized["topic"])),
        "PINGREQ": packet("PINGREQ"),
        "DISCONNECT": packet("DISCONNECT"),
    }
    for packet_name, encoded in samples.items():
        result = subprocess.run(
            [str(executable), "decode", encoded.hex()],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        decoded = json.loads(result.stdout)
        assert decoded["ok"] is True
        assert decoded["packet_type"] == packet_name

    result = subprocess.run(
        [
            str(executable),
            "encode",
            json.dumps({"packet_type": "PINGREQ", "fields": {}}),
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == packet("PINGREQ").hex()
