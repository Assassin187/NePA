"""Trusted sample oracle; never imported by the protocol-neutral generator."""
from __future__ import annotations
import argparse
import json
import socket
import time
import uuid


def receive(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise AssertionError(f"early EOF: {bytes(data).hex()}, wanted {count} bytes")
        data.extend(chunk)
    return bytes(data)


def connect_packet(client_id: str, level: int = 4) -> bytes:
    client = client_id.encode("ascii")
    body = b"\x00\x04MQTT" + bytes([level, 2, 0, 30]) + len(client).to_bytes(2, "big") + client
    length = len(body)
    encoded = bytearray()
    while True:
        digit, length = length % 128, length // 128
        encoded.append(digit | (128 if length else 0))
        if not length:
            break
    return b"\x10" + bytes(encoded) + body


def connect_ready(host: str, port: int) -> socket.socket:
    deadline = time.monotonic() + 5
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=2)
            sock.settimeout(2)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def exercise(host: str, port: int) -> list[str]:
    passed = []
    with connect_ready(host, port) as sock:
        packet = connect_packet("nepa-" + uuid.uuid4().hex[:12])
        sock.sendall(packet[:3])
        sock.sendall(packet[3:])
        reply = receive(sock, 4)
        assert reply == b"\x20\x02\x00\x00", f"invalid CONNACK: {reply.hex()}"
        passed.append("connect")
        sock.sendall(b"\xc0\x00")
        reply = receive(sock, 2)
        assert reply == b"\xd0\x00", f"invalid PINGRESP: {reply.hex()}"
        passed.append("ping")
        sock.sendall(b"\xe0\x00")
    with connect_ready(host, port) as sock:
        sock.sendall(connect_packet("reject-" + uuid.uuid4().hex[:10], level=0))
        reply = receive(sock, 4)
        assert reply == b"\x20\x02\x00\x01", f"invalid rejection: {reply.hex()}"
        assert sock.recv(1) == b"", "refused connection was not closed"
        passed.append("unsupported-level-reply-and-close")
    with connect_ready(host, port) as sock:
        sock.sendall(connect_packet("again-" + uuid.uuid4().hex[:10]))
        reply = receive(sock, 4)
        assert reply == b"\x20\x02\x00\x00", f"post-refusal CONNACK: {reply.hex()}"
        sock.sendall(b"\xc0\x00")
        assert receive(sock, 2) == b"\xd0\x00"
        sock.sendall(b"\xe0\x00")
        passed.append("subsequent-connection")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    try:
        checks = exercise(args.host, args.port)
        print(json.dumps({"passed": True, "checks": checks}))
        return 0
    except (AssertionError, OSError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
