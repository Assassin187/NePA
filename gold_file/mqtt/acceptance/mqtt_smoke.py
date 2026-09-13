"""Trusted sample oracle; never imported by the protocol-neutral generator."""
from __future__ import annotations
import argparse
import socket
from mqtt_behavior import Oracle, equal_bytes, receive, require, emit_result, WireMismatch


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


def expect_reply(reply, expected, category):
    try:
        equal_bytes(reply, expected, category)
    except WireMismatch as exc:
        exc.observation.update(expected_type=expected[0], actual_type=reply[0])
        if expected[0] == reply[0] == 0x20:
            exc.observation.update(expected_status=expected[3], actual_status=reply[3])
        raise


def exercise(host: str, port: int, oracle=None) -> list[str]:
    oracle = oracle if oracle is not None else Oracle.from_env()
    passed = []
    with oracle.connect(host, port) as sock:
        packet = connect_packet("nepa" + oracle.token(12))
        sock.sendall(packet[:3])
        sock.sendall(packet[3:])
        reply = receive(sock, 4)
        expect_reply(reply, b"\x20\x02\x00\x00", "connack")
        passed.append("connect")
        sock.sendall(b"\xc0\x00")
        reply = receive(sock, 2)
        expect_reply(reply, b"\xd0\x00", "pingresp")
        passed.append("ping")
        sock.sendall(b"\xe0\x00")
    with oracle.connect(host, port) as sock:
        sock.sendall(connect_packet("reject" + oracle.token(10), level=0))
        reply = receive(sock, 4)
        expect_reply(reply, b"\x20\x02\x00\x01", "refusal_connack")
        try:
            data = sock.recv(1)
        except socket.timeout:
            raise WireMismatch('refusal_close', expected_outcome='eof', actual_outcome='timeout') from None
        require(data == b"", "refusal_close", expected_outcome="eof", actual_outcome="data", actual_length=len(data))
        passed.append("unsupported-level-reply-and-close")
    with oracle.connect(host, port) as sock:
        sock.sendall(connect_packet("again" + oracle.token(10)))
        reply = receive(sock, 4)
        expect_reply(reply, b"\x20\x02\x00\x00", "subsequent_connack")
        sock.sendall(b"\xc0\x00")
        expect_reply(receive(sock, 2), b"\xd0\x00", "subsequent_pingresp")
        for _ in range(oracle.rng.randint(2, 4)):
            sock.sendall(b"\xc0\x00")
            expect_reply(receive(sock, 2), b"\xd0\x00", "subsequent_pingresp")
        sock.sendall(b"\xe0\x00")
        passed.append("subsequent-connection")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    return emit_result(lambda: exercise(args.host, args.port))



if __name__ == "__main__":
    raise SystemExit(main())
