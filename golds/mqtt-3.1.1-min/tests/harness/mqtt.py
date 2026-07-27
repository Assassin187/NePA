"""Small independent MQTT encoder/decoder used only by gold tests."""

from __future__ import annotations

import socket
from dataclasses import dataclass

from harness.spec_model import bit_const, field_const, fixed_byte, varint_max


def encode_varint(value: int) -> bytes:
    if not 0 <= value <= varint_max():
        raise ValueError("MQTT Remaining Length out of range")
    out = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        out.append(digit)
        if not value:
            return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    multiplier = 1
    value = 0
    for count in range(4):
        if offset + count >= len(data):
            raise ValueError("truncated MQTT varint")
        digit = data[offset + count]
        value += (digit & 0x7F) * multiplier
        if not digit & 0x80:
            return value, offset + count + 1
        multiplier *= 128
    raise ValueError("MQTT varint exceeds four bytes")


def utf8(value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > 65535:
        raise ValueError("MQTT UTF-8 string too long")
    return len(raw).to_bytes(2, "big") + raw


def packet(name: str, body: bytes = b"") -> bytes:
    return bytes([fixed_byte(name)]) + encode_varint(len(body)) + body


def connect(
    client_id: str,
    *,
    protocol_level: int | None = None,
    keep_alive: int = 30,
) -> bytes:
    protocol_name = str(field_const("CONNECT", "protocol_name"))
    level = (
        int(field_const("CONNECT", "protocol_level")) if protocol_level is None else protocol_level
    )
    connect_flags = (
        bit_const("CONNECT", "connect_flags", "clean_session") << 1
        | bit_const("CONNECT", "connect_flags", "will_flag") << 2
        | bit_const("CONNECT", "connect_flags", "password_flag") << 6
        | bit_const("CONNECT", "connect_flags", "username_flag") << 7
    )
    body = (
        utf8(protocol_name)
        + bytes([level, connect_flags])
        + keep_alive.to_bytes(2, "big")
        + utf8(client_id)
    )
    return packet("CONNECT", body)


def publish(topic: str, payload: bytes) -> bytes:
    return packet("PUBLISH", utf8(topic) + payload)


def subscribe(packet_id: int, topic: str) -> bytes:
    return packet("SUBSCRIBE", packet_id.to_bytes(2, "big") + utf8(topic) + b"\x00")


def unsubscribe(packet_id: int, topic: str) -> bytes:
    return packet("UNSUBSCRIBE", packet_id.to_bytes(2, "big") + utf8(topic))


@dataclass(frozen=True)
class ReceivedPacket:
    first_byte: int
    body: bytes

    @property
    def packet_type(self) -> int:
        return self.first_byte >> 4


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        chunk = sock.recv(size - len(out))
        if not chunk:
            raise EOFError("connection closed")
        out.extend(chunk)
    return bytes(out)


def recv_packet(sock: socket.socket) -> ReceivedPacket:
    first = _recv_exact(sock, 1)[0]
    remaining_bytes = bytearray()
    for _ in range(4):
        digit = _recv_exact(sock, 1)[0]
        remaining_bytes.append(digit)
        if not digit & 0x80:
            break
    else:
        raise ValueError("invalid Remaining Length")
    remaining, _ = decode_varint(bytes(remaining_bytes))
    return ReceivedPacket(first, _recv_exact(sock, remaining))
