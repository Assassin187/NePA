"""Independent wire scenarios for the existing MQTT QoS0 input, not full conformance."""
from __future__ import annotations
import argparse
import contextlib
import json
import socket
import time
import uuid


def remaining(length):
    result = bytearray()
    while True:
        digit, length = length % 128, length // 128
        result.append(digit | (128 if length else 0))
        if not length:
            return bytes(result)


def string(value):
    data = value.encode('utf-8') if isinstance(value, str) else value
    return len(data).to_bytes(2, 'big') + data


def frame(header, body=b''):
    return bytes([header]) + remaining(len(body)) + body


def connect_packet(identifier, keep_alive=30):
    return frame(0x10, b'\x00\x04MQTT\x04\x02' + keep_alive.to_bytes(2, 'big') + string(identifier))


def receive(sock, length):
    result = bytearray()
    while len(result) < length:
        chunk = sock.recv(length - len(result))
        assert chunk, f'early EOF: received {len(result)}/{length} bytes'
        result.extend(chunk)
    return bytes(result)


class Client:
    def __init__(self, host, port, identifier=None, keep_alive=30, handshake=True):
        deadline = time.monotonic() + 5
        while True:
            try:
                self.sock = socket.create_connection((host, port), timeout=2)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        self.sock.settimeout(2)
        self.identifier = identifier or ('n' + uuid.uuid4().hex[:20])
        if handshake:
            self.sock.sendall(connect_packet(self.identifier, keep_alive))
            self.expect(0x20, b'\x00\x00')

    def close(self):
        self.sock.close()

    def packet(self):
        header = receive(self.sock, 1)[0]
        length = 0
        for index in range(4):
            digit = receive(self.sock, 1)[0]
            length += (digit & 127) << (7 * index)
            if not digit & 128:
                assert length <= 1024 * 1024, 'unexpected oracle response size'
                return header, receive(self.sock, length)
        raise AssertionError('invalid response Remaining Length')

    def expect(self, header, body):
        actual = self.packet()
        assert actual == (header, body), f'expected {(header, body)!r}, got {actual!r}'

    def quiet(self, seconds=.15):
        self.sock.settimeout(seconds)
        try:
            data = self.sock.recv(1)
        except socket.timeout:
            return
        finally:
            self.sock.settimeout(2)
        raise AssertionError(f'expected open, quiet connection, got {data!r}')

    def eof(self, timeout=2):
        self.sock.settimeout(timeout)
        try:
            assert self.sock.recv(1) == b'', 'expected EOF without extra packet'
        except ConnectionResetError:
            # These cases require closing the network connection. The separate
            # minimum refusal oracle still requires its reply followed by EOF.
            return
        finally:
            self.sock.settimeout(2)

    def ping(self):
        self.sock.sendall(b'\xc0\x00')
        self.expect(0xd0, b'')

    def subscribe(self, topics, identifier=0x1234):
        self.sock.sendall(frame(0x82, identifier.to_bytes(2, 'big') + b''.join(string(t) + bytes([q]) for t, q in topics)))
        header, body = self.packet()
        assert header == 0x90 and body[:2] == identifier.to_bytes(2, 'big'), f'wrong SUBACK: {(header, body)!r}'
        codes = body[2:]
        assert len(codes) == len(topics), f'wrong SUBACK count: {codes!r}'
        assert all(code in (0, 1, 2, 128) for code in codes), f'illegal SUBACK: {codes!r}'
        assert all(code <= qos for code, (_, qos) in zip(codes, topics)), f'subscription not granted within requested QoS: {codes!r}'
        return codes

    def publish(self, topic, payload=b'hello', retain=False):
        self.sock.sendall(frame(0x31 if retain else 0x30, string(topic) + payload))

    def delivered(self, topic, payload=b'hello'):
        self.expect(0x30, string(topic) + payload)

    def unsubscribe(self, topics, identifier=0x4567):
        self.sock.sendall(frame(0xa2, identifier.to_bytes(2, 'big') + b''.join(string(t) for t in topics)))
        self.expect(0xb0, identifier.to_bytes(2, 'big'))


def exercise(case, host, port):
    topic = 'n/' + uuid.uuid4().hex
    with contextlib.ExitStack() as stack:
        def client(**kwargs):
            value = Client(host, port, **kwargs)
            stack.callback(value.close)
            return value
        if case in ('pubsub', 'multi_client', 'unsubscribe', 'session_isolation', 'qos'):
            sub, pub = client(), client()
            if case == 'qos':
                topics = [(topic + '/' + str(q), q) for q in range(3)]
                sub.subscribe(topics)
                for t, _ in topics:
                    pub.publish(t, b'\x00\xffbinary', retain=True)
                    sub.delivered(t, b'\x00\xffbinary')
            else:
                sub.subscribe([(topic, 0), (topic + '/other', 0)])
                if case == 'multi_client':
                    second, absent = client(), client()
                    second.subscribe([(topic, 0)])
                    absent.subscribe([(topic + '/unmatched', 0)])
                    pub.publish(topic)
                    sub.delivered(topic)
                    second.delivered(topic)
                    absent.quiet()
                    absent.ping()
                elif case == 'session_isolation':
                    second = client()
                    second.subscribe([(topic, 0)])
                    sub.unsubscribe([topic])
                    pub.publish(topic)
                    second.delivered(topic)
                    sub.quiet()
                    sub.ping()
                elif case == 'unsubscribe':
                    sub.unsubscribe([topic, topic + '/missing'])
                    pub.publish(topic)
                    pub.publish(topic + '/other')
                    sub.delivered(topic + '/other')
                    sub.quiet()
                    sub.unsubscribe([topic])
                else:
                    for payload in (b'', b'\x00\xff\xc0\x00binary'):
                        pub.publish(topic, payload)
                        sub.delivered(topic, payload)
                    pub.publish(topic + '/absent')
                    sub.quiet()
                    sub.ping()
        elif case == 'session_reset':
            identifier = 'n' + uuid.uuid4().hex[:20]
            old, pub = client(identifier=identifier), client()
            old.subscribe([(topic, 0)])
            pub.publish(topic)
            old.delivered(topic)
            old.sock.sendall(b'\xe0\x00')
            old.eof()
            new = client(identifier=identifier)
            pub.publish(topic)
            pub.ping()
            new.quiet()
            new.ping()
            new.subscribe([(topic, 0)])
            pub.publish(topic)
            new.delivered(topic)
        elif case == 'duplicate_connect':
            value = client()
            value.sock.sendall(connect_packet(value.identifier))
            value.eof()
            client().ping()
        elif case == 'disconnect':
            value = client()
            value.sock.sendall(b'\xe0\x00')
            value.eof()
            client().ping()
        elif case == 'invalid_qos':
            for qos in (3, 4, 128):
                value = client()
                value.sock.sendall(frame(0x82, b'\x00\x01' + string(topic) + bytes([qos])))
                value.eof()
            client().ping()
        elif case == 'fragmented_connect':
            value = client(handshake=False)
            packet = connect_packet(value.identifier)
            # Each incomplete prefix must leave the stream open without a reply.
            cuts = (1, 2, 3, 7, len(packet) - 1, len(packet))
            start = 0
            for end in cuts:
                value.sock.sendall(packet[start:end])
                if end < len(packet):
                    value.quiet(.06)
                start = end
            value.expect(0x20, b'\x00\x00')
            value.ping()
        elif case == 'fragmented_publish':
            sub, pub = client(), client()
            sub.subscribe([(topic, 0)])
            payload = b'z' * 160
            packet = frame(0x30, string(topic) + payload)
            start = 0
            for end in (1, 2, 3, 4, 5, 8, len(packet) - 1, len(packet)):
                pub.sock.sendall(packet[start:end])
                if end < len(packet):
                    sub.quiet(.06)
                    pub.quiet(.03)
                start = end
            sub.delivered(topic, payload)
            pub.ping()
        elif case == 'coalesced':
            sub, pub = client(), client()
            sub.subscribe([(topic, 0)])
            pub.sock.sendall(frame(0x30, string(topic) + b'one') + frame(0x30, string(topic) + b'two') + b'\xc0')
            sub.delivered(topic, b'one')
            sub.delivered(topic, b'two')
            pub.quiet(.1)
            pub.sock.sendall(b'\x00')
            pub.expect(0xd0, b'')
        elif case == 'length_boundaries':
            sub, pub = client(), client()
            sub.subscribe([(topic, 0)])
            for length in (127, 128, 16383, 16384):
                payload = b'z' * (length - len(string(topic)))
                pub.publish(topic, payload)
                sub.delivered(topic, payload)
        elif case == 'truncated':
            healthy, value = client(), client()
            value.sock.sendall(frame(0x30, string(topic) + b'body')[:-1])
            value.quiet(.1)
            value.sock.shutdown(socket.SHUT_WR)
            value.eof()
            healthy.ping()
            client().ping()
        elif case == 'invalid_flags':
            healthy = client()
            for packet in (b'\xc1\x00', frame(0x80, b'\x00\x01' + string(topic) + b'\x00'), b'\xe1\x00'):
                value = client()
                value.sock.sendall(packet)
                value.eof()
                healthy.ping()
            client().ping()
        elif case == 'invalid_utf8':
            healthy = client()
            for invalid in (b'\xc0\xaf', b'\xed\xa0\x80', b'\x00'):
                value = client()
                value.sock.sendall(frame(0x30, string(invalid) + b'payload'))
                value.eof()
                healthy.ping()
            client().ping()
        elif case == 'keep_alive':
            value = client(keep_alive=2)
            start = time.monotonic()
            value.eof(timeout=4)
            assert time.monotonic() - start >= 2.5, 'keep-alive disconnected early'
            client().ping()
        elif case == 'keep_alive_zero':
            value = client(keep_alive=0)
            value.quiet(4)
            value.ping()
        elif case == 'keep_alive_activity':
            value = client(keep_alive=2)
            for _ in range(5):
                time.sleep(1)
                value.ping()
            value.eof(timeout=4)
        else:
            raise ValueError(f'unknown case: {case}')


# Only observed server behavior is mapped; broad clauses remain scenario-limited.
CASES = {
    'pubsub': ['REQ-SUBSCRIBE-001', 'REQ-SUBSCRIBE-007', 'REQ-SUBSCRIBE-008', 'REQ-SUBSCRIBE-009', 'REQ-SUBACK-002', 'REQ-SUBACK-004', 'REQ-PUBLISH-005', 'REQ-PUBLISH-007', 'REQ-PUBLISH-009', 'REQ-TOPIC-002', 'REQ-SESSION-001', 'REQ-SESSION-002'],
    'multi_client': ['REQ-PUBLISH-009', 'REQ-TOPIC-003'],
    'unsubscribe': ['REQ-UNSUBSCRIBE-006', 'REQ-UNSUBSCRIBE-007', 'REQ-UNSUBACK-001', 'REQ-UNSUBACK-002', 'REQ-UNSUBACK-003', 'REQ-UNSUBACK-004'],
    'session_isolation': ['REQ-UNSUBSCRIBE-006', 'REQ-SESSION-002'],
    'qos': ['REQ-PUBLISH-002', 'REQ-PUBLISH-003', 'REQ-PUBLISH-010', 'REQ-SUBACK-004', 'REQ-SUBACK-006'],
    'session_reset': ['REQ-CONNECT-009', 'REQ-CONNECT-010', 'REQ-CONNECT-011', 'REQ-CONNACK-004'],
    'duplicate_connect': ['REQ-CONNECT-002', 'REQ-ERROR-001'],
    'disconnect': ['REQ-DISCONNECT-007'],
    'invalid_qos': ['REQ-SUBSCRIBE-006', 'REQ-ERROR-001'],
    'fragmented_connect': ['REQ-CONNECT-018'],
    'fragmented_publish': ['REQ-FRAME-001', 'REQ-FRAME-002', 'REQ-PUBLISH-009'],
    'coalesced': ['REQ-PUBLISH-009', 'REQ-PING-003'],
    'length_boundaries': ['REQ-FRAME-001', 'REQ-FRAME-002', 'REQ-PUBLISH-004'],
    'truncated': [],
    'invalid_flags': ['REQ-FRAME-004', 'REQ-SUBSCRIBE-002', 'REQ-DISCONNECT-002', 'REQ-ERROR-001'],
    'invalid_utf8': ['REQ-STRING-002', 'REQ-STRING-003', 'REQ-ERROR-001'],
    'keep_alive': ['REQ-KEEPALIVE-001'],
    'keep_alive_zero': ['REQ-KEEPALIVE-002'],
    'keep_alive_activity': ['REQ-KEEPALIVE-001', 'REQ-PING-003'],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--case', required=True, choices=CASES)
    args = parser.parse_args()
    try:
        exercise(args.case, args.host, args.port)
        print(json.dumps({'case': args.case, 'passed': True}))
        return 0
    except (AssertionError, OSError, ValueError) as exc:
        print(json.dumps({'case': args.case, 'passed': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
