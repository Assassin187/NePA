"""L2 black-box broker behavior over loopback TCP."""

from __future__ import annotations

import socket
import subprocess
import time

import pytest
from harness.cli_target import publish_command, subscribe_command
from harness.mqtt import (
    connect,
    packet,
    publish,
    recv_packet,
    subscribe,
    unsubscribe,
)
from harness.spec_model import named_constant, packet_type


def _connected(broker, client_id: str, *, keep_alive: int = 30) -> socket.socket:
    sock = broker.connect()
    sock.sendall(connect(client_id, keep_alive=keep_alive))
    connack = recv_packet(sock)
    assert connack.packet_type == packet_type("CONNACK")
    assert connack.body == b"\x00\x00"
    return sock


def _expect_eof(sock: socket.socket, timeout: float = 2.5) -> None:
    sock.settimeout(timeout)
    try:
        assert sock.recv(1) == b""
    except ConnectionResetError:
        pass


@pytest.mark.req("REQ-CONNECT-001")
@pytest.mark.req("REQ-CONNECT-004")
@pytest.mark.req("REQ-CONNACK-001")
def test_connect_success(broker, randomized: dict[str, object]) -> None:
    """A valid randomized client id receives successful clean-session CONNACK."""
    with _connected(broker, str(randomized["client_a"])) as sock:
        sock.sendall(packet("DISCONNECT"))


@pytest.mark.req("REQ-CONNECT-002")
def test_bad_protocol_level(broker, randomized: dict[str, object]) -> None:
    """An unsupported protocol level receives rc=1 then disconnection."""
    with broker.connect() as sock:
        sock.sendall(connect(str(randomized["client_a"]), protocol_level=9))
        connack = recv_packet(sock)
        assert connack.packet_type == packet_type("CONNACK")
        assert connack.body == b"\x00\x01"
        _expect_eof(sock)


@pytest.mark.req("REQ-SUBSCRIBE-001")
@pytest.mark.req("REQ-SUBACK-001")
def test_subscribe_acknowledged(broker, randomized: dict[str, object]) -> None:
    """A literal QoS0 subscription is acknowledged with the request packet id."""
    with _connected(broker, str(randomized["client_a"])) as sock:
        sock.sendall(subscribe(7, str(randomized["topic"])))
        suback = recv_packet(sock)
        assert suback.packet_type == packet_type("SUBACK")
        assert suback.body == b"\x00\x07\x00"


@pytest.mark.req("REQ-PUBLISH-001")
@pytest.mark.req("REQ-ROUTE-001")
def test_publish_is_forwarded(broker, randomized: dict[str, object]) -> None:
    """QoS0 data reaches exactly the subscriber for the randomized literal topic."""
    topic = str(randomized["topic"])
    payload = bytes(randomized["payload"])
    with (
        _connected(broker, str(randomized["client_a"])) as subscriber,
        _connected(broker, str(randomized["client_b"])) as publisher,
    ):
        subscriber.sendall(subscribe(7, topic))
        assert recv_packet(subscriber).packet_type == packet_type("SUBACK")
        publisher.sendall(publish(topic, payload))
        delivered = recv_packet(subscriber)
        assert delivered.packet_type == packet_type("PUBLISH")
        topic_len = int.from_bytes(delivered.body[:2], "big")
        assert delivered.body[2 : 2 + topic_len].decode() == topic
        assert delivered.body[2 + topic_len :] == payload
        subscriber.settimeout(0.2)
        with pytest.raises(socket.timeout):
            subscriber.recv(1)


@pytest.mark.req("REQ-UNSUBSCRIBE-001")
@pytest.mark.req("REQ-UNSUBACK-001")
def test_unsubscribe_stops_delivery(broker, randomized: dict[str, object]) -> None:
    """Exact unsubscription is acknowledged and prevents later delivery."""
    topic = str(randomized["topic"])
    with (
        _connected(broker, str(randomized["client_a"])) as subscriber,
        _connected(broker, str(randomized["client_b"])) as publisher,
    ):
        subscriber.sendall(subscribe(7, topic))
        assert recv_packet(subscriber).packet_type == packet_type("SUBACK")
        subscriber.sendall(unsubscribe(8, topic))
        unsuback = recv_packet(subscriber)
        assert unsuback.packet_type == packet_type("UNSUBACK")
        assert unsuback.body == b"\x00\x08"
        publisher.sendall(publish(topic, bytes(randomized["payload"])))
        subscriber.settimeout(0.4)
        with pytest.raises(socket.timeout):
            subscriber.recv(1)


@pytest.mark.req("REQ-PING-001")
def test_ping_response(broker, randomized: dict[str, object]) -> None:
    """PINGREQ receives the zero-length PINGRESP packet."""
    with _connected(broker, str(randomized["client_a"])) as sock:
        sock.sendall(packet("PINGREQ"))
        response = recv_packet(sock)
        assert response.packet_type == packet_type("PINGRESP")
        assert response.body == b""


@pytest.mark.req("REQ-DISCONNECT-001")
def test_disconnect_closes_session(broker, randomized: dict[str, object]) -> None:
    """DISCONNECT ends the current network connection."""
    with _connected(broker, str(randomized["client_a"])) as sock:
        sock.sendall(packet("DISCONNECT"))
        _expect_eof(sock)


@pytest.mark.req("REQ-STATE-001")
def test_duplicate_connect_disconnects(broker, randomized: dict[str, object]) -> None:
    """A second CONNECT on one network connection is a protocol violation."""
    with _connected(broker, str(randomized["client_a"])) as sock:
        sock.sendall(connect(str(randomized["client_a"])))
        _expect_eof(sock)


@pytest.mark.req("REQ-FRAME-002")
@pytest.mark.req("REQ-ERROR-001")
def test_invalid_fixed_header_disconnects(broker, randomized: dict[str, object]) -> None:
    """Invalid SUBSCRIBE flags close only the offending connection."""
    with _connected(broker, str(randomized["client_a"])) as bad:
        malformed = bytearray(subscribe(7, str(randomized["topic"])))
        malformed[0] &= 0xF0
        bad.sendall(malformed)
        _expect_eof(bad)
    with _connected(broker, str(randomized["client_b"])) as healthy:
        healthy.sendall(packet("PINGREQ"))
        assert recv_packet(healthy).packet_type == packet_type("PINGRESP")


@pytest.mark.req("REQ-KEEPALIVE-001")
def test_keep_alive_timeout(
    broker,
    randomized: dict[str, object],
    target: str,
) -> None:
    """Non-zero Keep Alive disconnects an idle client within scheduling tolerance."""
    with _connected(broker, str(randomized["client_a"]), keep_alive=1) as sock:
        start = time.monotonic()
        protocol_timeout = float(named_constant("keep_alive_multiplier"))
        # Mosquitto 2.0 performs idle-client expiry in a coarse maintenance pass.
        # Keep the generated target's allowance narrow while permitting that known
        # reference scheduling granularity during the D0.2 oracle audit.
        scheduling_tolerance = 5.5 if target == "reference" else 1.0
        deadline = start + protocol_timeout + scheduling_tolerance
        sock.settimeout(0.25)
        while time.monotonic() < deadline:
            try:
                data = sock.recv(1)
            except TimeoutError:
                continue
            assert data == b""
            assert time.monotonic() - start >= protocol_timeout
            return
        pytest.fail("broker did not enforce keep_alive within the allowed deadline")


@pytest.mark.req("REQ-PUBLISH-001")
@pytest.mark.req("REQ-ROUTE-001")
def test_client_cli_publish_contract(
    broker,
    randomized: dict[str, object],
    target: str,
    workspace,
) -> None:
    """Client pub command maps to the frozen external contract."""
    topic = str(randomized["topic"])
    message = bytes(randomized["payload"]).decode()
    with _connected(broker, str(randomized["client_a"])) as subscriber:
        subscriber.sendall(subscribe(7, topic))
        assert recv_packet(subscriber).packet_type == packet_type("SUBACK")
        result = subprocess.run(
            publish_command(target, workspace, "127.0.0.1", broker.port, topic, message),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        delivered = recv_packet(subscriber)
        assert delivered.body.endswith(message.encode())


@pytest.mark.req("REQ-SUBSCRIBE-001")
@pytest.mark.req("REQ-ROUTE-001")
def test_client_cli_subscribe_contract(
    broker,
    randomized: dict[str, object],
    target: str,
    workspace,
) -> None:
    """Client sub command prints one tab-separated topic/payload line."""
    topic = str(randomized["topic"])
    message = bytes(randomized["payload"]).decode()
    process = subprocess.Popen(
        subscribe_command(target, workspace, "127.0.0.1", broker.port, topic, 1, 4),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.3)
        with _connected(broker, str(randomized["client_b"])) as publisher:
            publisher.sendall(publish(topic, message.encode()))
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert stdout.strip() == f"{topic}\t{message}"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=1)
