"""Independent wire scenarios for the existing MQTT QoS0 input, not full conformance."""
from __future__ import annotations
import argparse
import contextlib
import json
import os
import random
import sys
import socket
import time


class WireMismatch(AssertionError):
    """Only these explicitly constructed semantic fields may cross the host boundary."""
    def __init__(self, category, **observation):
        super().__init__(category)
        self.category, self.observation = category, observation


def require(condition, category, **observation):
    if not condition:
        raise WireMismatch(category, **observation)


def equal_bytes(actual, expected, category):
    require(actual == expected, category,
            expected_length=len(expected), actual_length=len(actual),
            offset=next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
                        min(len(actual), len(expected))),
            expected_outcome='matching_bytes', actual_outcome='different_bytes')


class TraceSocket:
    def __init__(self, sock, oracle, connection):
        self.sock, self.oracle, self.connection = sock, oracle, connection
        self.sent = self.received = self.writes = 0

    def observe(self, event, **fields):
        self.oracle.record(event, connection=self.connection, **fields)

    def sendall(self, data):
        self.writes += 1
        self.observe('write', write=self.writes, requested=len(data))
        offset = 0
        while offset < len(data):
            try:
                count = self.sock.send(data[offset:])
            except OSError as exc:
                self.observe('send', outcome='timeout' if isinstance(exc, socket.timeout) else 'error',
                             count=0, offset=self.sent, write=self.writes)
                raise
            self.observe('send', outcome='data' if count else 'eof', count=count,
                         offset=self.sent, write=self.writes, data_hex=data[offset:offset + count].hex())
            require(count > 0, 'send_closed', expected_outcome='sent', actual_outcome='eof',
                    expected_length=len(data), actual_length=offset)
            offset += count
            self.sent += count

    def recv(self, count):
        try:
            data = self.sock.recv(count)
        except OSError as exc:
            self.observe('recv', outcome='timeout' if isinstance(exc, socket.timeout) else 'error',
                         requested=count, count=0, offset=self.received)
            raise
        self.observe('recv', outcome='data' if data else 'eof', requested=count,
                     count=len(data), offset=self.received, data_hex=data.hex())
        self.received += len(data)
        return data

    def settimeout(self, seconds):
        self.sock.settimeout(seconds)
        self.observe('settimeout', seconds=seconds)

    def shutdown(self, how):
        self.sock.shutdown(how)
        self.observe('half_close', how=how)

    def close(self):
        self.sock.close()
        self.observe('close')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Oracle:
    """Case-local generator and private actual-I/O journal; no global random state."""
    def __init__(self, seed, trace_file=None):
        if len(seed) != 64 or any(c not in '0123456789abcdefABCDEF' for c in seed):
            raise ValueError('oracle seed must be 256-bit hexadecimal')
        self.rng = random.Random(int(seed, 16))
        self.trace_file, self.connections, self.sequence = trace_file, 0, 0
        self.record('start', seed=seed, randomization_version='random-inputs/1', python_version=sys.version.split()[0])

    @classmethod
    def from_env(cls):
        return cls(os.environ['NEPA_ORACLE_SEED'], os.environ['NEPA_ORACLE_TRACE_FILE'])

    def record(self, event, **fields):
        self.sequence += 1
        if self.trace_file is not None:
            with open(self.trace_file, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(event=event, sequence=self.sequence, timestamp_ns=time.time_ns(),
                                             monotonic_ns=time.monotonic_ns(), **fields)) + '\n')

    def token(self, length=16):
        return ''.join(self.rng.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(length))

    def payload(self):
        return bytes(self.rng.randrange(256) for _ in range(self.rng.randint(1, 96)))

    def cuts(self, length, mandatory):
        return sorted(set(mandatory) | set(self.rng.sample(range(1, length), self.rng.randint(2, 4))))

    def connect(self, host, port):
        self.connections += 1
        connection = self.connections
        deadline = time.monotonic() + 5
        while True:
            try:
                sock = socket.create_connection((host, port), timeout=2)
                break
            except OSError as exc:
                self.record('connect', connection=connection, outcome='error', error_class=type(exc).__name__)
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        self.record('connect', connection=connection, outcome='connected')
        value = TraceSocket(sock, self, connection)
        value.settimeout(2)
        return value


def emit_result(callback):
    try:
        callback()
        result = dict(passed=True, category='protocol', observation={'outcome': 'accepted'})
    except WireMismatch as exc:
        result = dict(passed=False, category=exc.category, observation=exc.observation)
    except Exception as exc:
        # Exception text can contain response bytes, IDs, seed or a private filename.
        error_class = type(exc).__name__
        if error_class not in {'AssertionError', 'ValueError', 'KeyError', 'TypeError',
                               'TimeoutError', 'ConnectionResetError', 'ConnectionRefusedError',
                               'BrokenPipeError', 'OSError', 'FileNotFoundError', 'PermissionError'}:
            error_class = 'Exception'
        result = dict(passed=False, category='timeout' if isinstance(exc, socket.timeout) else 'error',
                      observation={'error_class': error_class, 'actual_outcome': 'failed'})
    print(json.dumps(result))
    return 0 if result['passed'] else 1


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
        try:
            chunk = sock.recv(length - len(result))
        except socket.timeout:
            raise WireMismatch('receive_timeout', expected_length=length, actual_length=len(result),
                               expected_outcome='complete_packet', actual_outcome='timeout') from None
        require(bool(chunk), 'early_EOF', expected_length=length, actual_length=len(result),
                expected_outcome='complete_packet', actual_outcome='eof')
        result.extend(chunk)
    return bytes(result)


class Client:
    def __init__(self, host, port, identifier=None, keep_alive=30, handshake=True, oracle=None):
        self.oracle = oracle if oracle is not None else Oracle.from_env()
        self.sock = self.oracle.connect(host, port)
        self.identifier = identifier or ('n' + self.oracle.token(self.oracle.rng.randint(8, 20)))
        self.order = 0
        if handshake:
            self.sock.sendall(connect_packet(self.identifier, keep_alive))
            self.expect(0x20, b'\x00\x00')

    def close(self):
        self.sock.close()

    def packet(self):
        self.order += 1
        try:
            header = receive(self.sock, 1)[0]
            length = 0
            for index in range(4):
                digit = receive(self.sock, 1)[0]
                length += (digit & 127) << (7 * index)
                if not digit & 128:
                    require(length <= 1024 * 1024, 'response_size',
                            expected_length=1024 * 1024, actual_length=length)
                    return header, receive(self.sock, length)
            raise WireMismatch('remaining_length', expected_length=4, actual_length=4,
                               expected_outcome='terminated', actual_outcome='continuation')
        except WireMismatch as exc:
            exc.observation['actual_order'] = self.order
            raise

    def expect(self, header, body):
        try:
            actual_header, actual_body = self.packet()
        except WireMismatch as exc:
            exc.observation['expected_type'] = header
            raise
        require(actual_header == header, 'expected_packet_type',
                expected_type=header, actual_type=actual_header, actual_order=self.order)
        try:
            equal_bytes(actual_body, body, 'expected_packet_body')
        except WireMismatch as exc:
            exc.observation.update(expected_type=header, actual_type=actual_header, actual_order=self.order)
            if header == 0x20 and len(actual_body) >= 2:
                exc.observation.update(expected_status=body[1], actual_status=actual_body[1])
            raise

    def quiet(self, seconds=.15):
        self.sock.settimeout(seconds)
        try:
            data = self.sock.recv(1)
        except socket.timeout:
            self.sock.observe('quiet', seconds=seconds, outcome='quiet')
            return
        finally:
            self.sock.settimeout(2)
        self.sock.observe('quiet', seconds=seconds, outcome='data' if data else 'eof')
        raise WireMismatch('quiet', expected_outcome='open_quiet',
                           actual_outcome='data' if data else 'eof', actual_length=len(data))

    def eof(self, timeout=2):
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(1)
            require(data == b'', 'connection_close', expected_outcome='eof',
                    actual_outcome='data', actual_length=len(data))
        except ConnectionResetError:
            return
        except socket.timeout:
            raise WireMismatch('connection_close', expected_outcome='eof', actual_outcome='timeout') from None
        finally:
            self.sock.settimeout(2)

    def ping(self):
        self.sock.sendall(b'\xc0\x00')
        self.expect(0xd0, b'')

    def subscribe(self, topics, identifier=None):
        if identifier is None:
            identifier = self.oracle.rng.randint(1, 65535)
        self.sock.sendall(frame(0x82, identifier.to_bytes(2, 'big') + b''.join(string(t) + bytes([q]) for t, q in topics)))
        header, body = self.packet()
        require(header == 0x90, 'suback_type', expected_type=0x90, actual_type=header, actual_order=self.order)
        require(body[:2] == identifier.to_bytes(2, 'big'), 'suback_identifier',
                expected_outcome='matching_identifier', actual_outcome='mismatched_identifier',
                expected_length=2, actual_length=len(body[:2]), actual_order=self.order)
        codes = body[2:]
        require(len(codes) == len(topics), 'suback_count', expected_length=len(topics), actual_length=len(codes))
        for index, (code, (_, qos)) in enumerate(zip(codes, topics)):
            require(code in (0, 1, 2, 128), 'suback_code', expected_outcome='legal_return_code',
                    actual_type=code, offset=index)
            require(code <= qos, 'suback_qos', expected_type=qos, actual_type=code, offset=index)
        return codes

    def publish(self, topic, payload=b'hello', retain=False):
        self.sock.sendall(frame(0x31 if retain else 0x30, string(topic) + payload))

    def delivered(self, topic, payload=b'hello'):
        self.expect(0x30, string(topic) + payload)

    def unsubscribe(self, topics, identifier=None):
        if identifier is None:
            identifier = self.oracle.rng.randint(1, 65535)
        self.sock.sendall(frame(0xa2, identifier.to_bytes(2, 'big') + b''.join(string(t) for t in topics)))
        self.expect(0xb0, identifier.to_bytes(2, 'big'))


def exercise(case, host, port, oracle=None):
    oracle = oracle if oracle is not None else Oracle.from_env()
    topic = 'n/' + oracle.token(oracle.rng.randint(8, 32))
    with contextlib.ExitStack() as stack:
        def client(**kwargs):
            value = Client(host, port, oracle=oracle, **kwargs)
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
                for _ in range(oracle.rng.randint(2, 4)):
                    t, _ = oracle.rng.choice(topics)
                    payload = oracle.payload()
                    pub.publish(t, payload, retain=True)
                    sub.delivered(t, payload)
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
                    for _ in range(oracle.rng.randint(2, 4)):
                        payload = oracle.payload()
                        pub.publish(topic, payload)
                        sub.delivered(topic, payload)
                        second.delivered(topic, payload)
                        absent.quiet()
                elif case == 'session_isolation':
                    second = client()
                    second.subscribe([(topic, 0)])
                    sub.unsubscribe([topic])
                    pub.publish(topic)
                    second.delivered(topic)
                    sub.quiet()
                    sub.ping()
                    for _ in range(oracle.rng.randint(2, 4)):
                        payload = oracle.payload()
                        pub.publish(topic, payload)
                        second.delivered(topic, payload)
                        sub.quiet()
                elif case == 'unsubscribe':
                    sub.unsubscribe([topic, topic + '/missing'])
                    pub.publish(topic)
                    pub.publish(topic + '/other')
                    sub.delivered(topic + '/other')
                    sub.quiet()
                    sub.unsubscribe([topic])
                    for _ in range(oracle.rng.randint(2, 4)):
                        payload = oracle.payload()
                        pub.publish(topic, payload)
                        pub.publish(topic + '/other', payload)
                        sub.delivered(topic + '/other', payload)
                        sub.quiet()
                else:
                    for payload in (b'', b'\x00\xff\xc0\x00binary',
                                    *(oracle.payload() for _ in range(oracle.rng.randint(2, 4)))):
                        pub.publish(topic, payload)
                        sub.delivered(topic, payload)
                    pub.publish(topic + '/absent')
                    sub.quiet()
                    sub.ping()
        elif case == 'session_reset':
            identifier = 'n' + oracle.token(20)
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
            for _ in range(oracle.rng.randint(2, 4)):
                payload = oracle.payload()
                pub.publish(topic, payload)
                new.delivered(topic, payload)
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
            cuts = oracle.cuts(len(packet), (1, 2, 3, 7, len(packet) - 1, len(packet)))
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
            for end in oracle.cuts(len(packet), (1, 2, 3, 4, 5, 8, len(packet) - 1, len(packet))):
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
            payloads = [oracle.payload() for _ in range(oracle.rng.randint(2, 4))]
            start = 0
            while start < len(payloads):
                size = oracle.rng.randint(2, 4)
                group = payloads[start:start + size]
                pub.sock.sendall(b''.join(frame(0x30, string(topic) + p) for p in group))
                for payload in group:
                    sub.delivered(topic, payload)
                start += len(group)
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
            elapsed = time.monotonic() - start
            require(elapsed >= 2.5, 'keep_alive_early_close', expected_duration_ms=2500,
                    actual_duration_ms=int(elapsed * 1000))
            client().ping()
        elif case == 'keep_alive_zero':
            value = client(keep_alive=0)
            value.quiet(4)
            value.ping()
        elif case == 'keep_alive_activity':
            value = client(keep_alive=2)
            for _ in range(5):
                oracle.record('wait', seconds=1)
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
    return emit_result(lambda: exercise(args.case, args.host, args.port))



if __name__ == '__main__':
    raise SystemExit(main())
