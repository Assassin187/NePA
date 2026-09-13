"""Offline vector/diagnostic tests. These doubles are not server acceptance evidence."""
import importlib.util
import json
from pathlib import Path
import re
import socket
import sys

import pytest

from test_protocol_oracles import client, oracle

ROOT = Path(__file__).parents[1]
SEED = '0123456789abcdef' * 4


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def time_ns(self):
        return int(self.now * 1e9)

    monotonic_ns = time_ns


class Peer:
    """An in-memory peer with real stream accumulation and deliberately short reads."""
    def __init__(self, network):
        self.network = network
        self.incoming = self.outgoing = b''
        self.closed = False
        self.timeout = 2
        self.keep_alive = 0
        self.last_activity = network.clock.now
        self.topics = set()

    def settimeout(self, seconds):
        self.timeout = seconds

    def send(self, data):
        self.incoming += data
        self.last_activity = self.network.clock.now
        self.network.process(self)
        return len(data)

    def recv(self, count):
        if self.outgoing:
            value, self.outgoing = self.outgoing[:min(count, 3)], self.outgoing[min(count, 3):]
            return value
        if self.closed:
            return b''
        if self.keep_alive:
            expiry = self.last_activity + self.keep_alive * 1.5
            if expiry <= self.network.clock.now + self.timeout:
                self.network.clock.now = max(expiry, self.network.clock.now)
                self.closed = True
                return b''
        self.network.clock.sleep(self.timeout)
        raise socket.timeout()

    def shutdown(self, how):
        assert how == socket.SHUT_WR
        self.closed = True

    def close(self):
        self.closed = True


class Network:
    def __init__(self, module, protocol, clock):
        self.module, self.protocol, self.clock = module, protocol, clock
        self.peers = []
        self.client_ids = []
        self.packets = []

    def connect(self, address, timeout):
        assert address == ('localhost', 49123) and timeout == 2
        peer = Peer(self)
        self.peers.append(peer)
        return peer

    def process(self, peer):
        if self.protocol == 'mqtt':
            self.mqtt(peer)
        else:
            self.http(peer)

    def mqtt(self, peer):
        while len(peer.incoming) >= 2:
            header = peer.incoming[0]
            length = 0
            pos = 1
            while pos < len(peer.incoming):
                digit = peer.incoming[pos]
                length += (digit & 127) << (7 * (pos - 1))
                pos += 1
                if not digit & 128:
                    break
            else:
                return
            if len(peer.incoming) < pos + length:
                return
            body = peer.incoming[pos:pos + length]
            peer.incoming = peer.incoming[pos + length:]
            self.packets.append((header, body))
            if header == 0x10:
                if peer.keep_alive:
                    peer.closed = True
                elif body[6] != 4:
                    peer.outgoing += b'\x20\x02\x00\x01'
                    peer.closed = True
                else:
                    self.client_ids.append(body[12:])
                    peer.keep_alive = int.from_bytes(body[8:10], 'big')
                    peer.outgoing += b'\x20\x02\x00\x00'
            elif header == 0xc0:
                peer.outgoing += b'\xd0\x00'
            elif header == 0xe0:
                peer.closed = True
            elif header in (0x82, 0xa2):
                pos, codes = 2, b''
                while pos < len(body):
                    size = int.from_bytes(body[pos:pos + 2], 'big')
                    topic = body[pos + 2:pos + 2 + size]
                    pos += 2 + size
                    if header == 0x82:
                        if body[pos] not in (0, 1, 2):
                            peer.closed = True
                            break
                        peer.topics.add(topic)
                        codes += b'\x00'
                        pos += 1
                    else:
                        peer.topics.discard(topic)
                if not peer.closed:
                    peer.outgoing += self.module.frame(0x90 if header == 0x82 else 0xb0, body[:2] + codes)
            elif header in (0x30, 0x31):
                size = int.from_bytes(body[:2], 'big')
                topic = body[2:2 + size]
                try:
                    decoded = topic.decode('utf-8')
                except UnicodeDecodeError:
                    peer.closed = True
                    continue
                if '\x00' in decoded:
                    peer.closed = True
                    continue
                for sub in self.peers:
                    if not sub.closed and topic in sub.topics:
                        sub.outgoing += self.module.frame(0x30, body)
            else:
                peer.closed = True

    def http(self, peer):
        while b'\r\n\r\n' in peer.incoming:
            raw, body = peer.incoming.split(b'\r\n\r\n', 1)
            lines = raw.split(b'\r\n')
            first = lines.pop(0).split(b' ')
            fields = {}
            bad = len(first) != 3 or first[-1] != b'HTTP/1.1'
            for line in lines:
                if b':' not in line:
                    bad = True
                    continue
                name, value = line.split(b':', 1)
                name = name.lower()
                if name != name.strip() or name in fields:
                    bad = True
                fields[name] = value.strip()
            host = fields.get(b'host', b'')
            value = fields.get(b'content-length', b'0')
            bad |= not host or b' ' in host or b'transfer-encoding' in fields
            bad |= not value.isdigit() or len(value) > 10
            if bad:
                peer.outgoing += b'HTTP/1.1 400 Bad\r\nContent-Length: 0\r\n\r\n'
                peer.closed = True
                peer.incoming = b''
                return
            length = int(value)
            if len(body) < length:
                return
            payload, peer.incoming = body[:length], body[length:]
            method, path, _ = first
            head = method == b'HEAD'
            if method not in (b'GET', b'HEAD', b'POST'):
                status, result = 501, b''
            elif path == b'/' and method in (b'GET', b'HEAD'):
                status, result = 200, b'nepa\n'
            elif path == b'/echo' and method == b'POST':
                status, result = 200, payload
            else:
                status, result = 404, b''
            close = fields.get(b'connection', b'').lower() == b'close'
            peer.outgoing += f'HTTP/1.1 {status} Result\r\nContent-Length: {len(result)}\r\n'.encode()
            peer.outgoing += b'Connection: close\r\n' if close else b''
            peer.outgoing += b'\r\n' + (b'' if head else result)
            peer.closed |= close


def simulate(monkeypatch, protocol, case, seed):
    module = oracle(protocol)
    clock = Clock()
    network = Network(module, protocol, clock)
    monkeypatch.setattr(module, 'time', clock)
    monkeypatch.setattr(module.socket, 'create_connection', network.connect)
    run = module.Oracle(seed)
    events = []
    run.record = lambda event, **fields: events.append(dict(event=event, **fields))
    if case == 'minimum-interactions':
        monkeypatch.setitem(sys.modules, 'mqtt_behavior', module)
        spec = importlib.util.spec_from_file_location('smoke_oracle', ROOT / 'gold_file/mqtt/acceptance/mqtt_smoke.py')
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        smoke.exercise('localhost', 49123, oracle=run)
    else:
        module.exercise(case, 'localhost', 49123, oracle=run)
    return events, network


ALL_CASES = [(p, c['id']) for p in ('mqtt', 'http')
             for c in json.loads((ROOT / f'gold_file/{p}/acceptance.json').read_text())['checks']]


@pytest.mark.parametrize('protocol,case', ALL_CASES)
def test_all_cases_replay_seed_and_keep_original_checks(monkeypatch, protocol, case):
    first, network = simulate(monkeypatch, protocol, case, SEED)
    again, _ = simulate(monkeypatch, protocol, case, SEED)
    other, _ = simulate(monkeypatch, protocol, case, 'f0' * 32)
    assert first == again
    assert [e for e in first if e['event'] == 'send'] != [e for e in other if e['event'] == 'send']
    assert network.peers and all(peer.closed for peer in network.peers)
    assert all(re.fullmatch(rb'[A-Za-z0-9]{1,23}', identifier) for identifier in network.client_ids)
    if case == 'keep_alive_zero':
        assert any(e['event'] == 'quiet' and e['seconds'] == 4 for e in first)
    if case == 'keep_alive_activity':
        assert network.clock.now >= 8


@pytest.mark.parametrize('protocol', ['mqtt', 'http'])
def test_trace_actual_short_io_eof_timeout_halfclose(tmp_path, protocol):
    module = oracle(protocol)
    class ShortSocket:
        def __init__(self):
            self.parts = iter([b'a', b'bc', b'', socket.timeout()])
        def send(self, data):
            return min(2, len(data))
        def recv(self, count):
            part = next(self.parts)
            if isinstance(part, Exception):
                raise part
            return part
        def settimeout(self, seconds):
            pass
        def shutdown(self, how):
            pass
        def close(self):
            pass
    path = tmp_path / 'private.jsonl'
    run = module.Oracle(SEED, path)
    sock = module.TraceSocket(ShortSocket(), run, 1)
    sock.settimeout(.08)
    sock.sendall(b'abcde')
    assert [sock.recv(10), sock.recv(10), sock.recv(10)] == [b'a', b'bc', b'']
    with pytest.raises(socket.timeout):
        sock.recv(10)
    sock.shutdown(socket.SHUT_WR)
    sock.close()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]['seed'] == SEED
    assert [r['sequence'] for r in records] == list(range(1, len(records) + 1))
    assert all(isinstance(r['timestamp_ns'], int) and isinstance(r['monotonic_ns'], int) for r in records)
    sent = [r for r in records if r['event'] == 'send']
    assert [(r['count'], r['offset'], r['data_hex']) for r in sent] == [(2, 0, '6162'), (2, 2, '6364'), (1, 4, '65')]
    received = [r for r in records if r['event'] == 'recv']
    assert [(r['outcome'], r['count'], r['offset']) for r in received] == [('data', 1, 0), ('data', 2, 1), ('eof', 0, 3), ('timeout', 0, 3)]
    assert records[-2]['event'] == 'half_close' and records[-2]['how'] == socket.SHUT_WR


@pytest.mark.parametrize('protocol', ['mqtt', 'http'])
def test_safe_diagnostic_never_serializes_untrusted_text(capsys, protocol):
    module = oracle(protocol)
    secret = 'PRIVATE /checks/private.py seed=' + SEED + ' payload=clientID/topic'
    def fail():
        raise ValueError(secret)
    assert module.emit_result(fail) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert set(result) == {'passed', 'category', 'observation'}
    assert result == {'passed': False, 'category': 'error', 'observation': {'error_class': 'ValueError', 'actual_outcome': 'failed'}}
    assert secret not in output.out and SEED not in output.out
    assert len(output.out.splitlines()) == 1
    assert module.emit_result(lambda: None) == 0
    assert json.loads(capsys.readouterr().out)['passed'] is True


def test_semantic_failures_contain_status_type_length_order_offset(capsys):
    m, h = oracle('mqtt'), oracle('http')
    cases = [
        (m, lambda: client(m, m.frame(0x38, b'private')).delivered('private'), 'expected_packet_type', {'actual_type': 0x38, 'expected_type': 0x30}),
        (m, lambda: client(m, m.frame(0x90, b'\x12\x34')).subscribe([('secret', 0)], identifier=0x1234), 'suback_count', {'actual_length': 0, 'expected_length': 1}),
        (m, lambda: client(m, m.frame(0x90, b'\x12\x34\x80')).subscribe([('secret', 0)], identifier=0x1234), 'suback_qos', {'actual_type': 128, 'expected_type': 0}),
        (h, lambda: client(h, b'HTTP/1.1 404 Wrong\r\nContent-Length: 0\r\n\r\n').expect(200), 'response_status', {'expected_status': 200, 'actual_status': 404, 'actual_order': 1}),
        (h, lambda: client(h, b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\naXc').expect(200, b'abc'), 'response_body', {'expected_length': 3, 'actual_length': 3, 'offset': 1}),
        (h, lambda: client(h, b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nabc').expect(200, b'abcd'), 'body_EOF', {'actual_length': 3, 'expected_length': 4}),
        (m, lambda: client(m, b'\xd0').expect(0xd0, b''), 'early_EOF', {'expected_length': 1, 'actual_length': 0}),
        (m, lambda: client(m, b'').expect(0x30, b'z' * 16384), 'early_EOF',
         {'expected_length': 1, 'actual_length': 0, 'expected': {'length': 16384, 'stage': 'packet_body'}}),
        (h, lambda: client(h, b'private').quiet(), 'quiet', {'actual_outcome': 'data', 'actual_length': 1}),
    ]
    for module, callback, category, fields in cases:
        assert module.emit_result(callback) == 1
        output = capsys.readouterr().out
        result = json.loads(output)
        assert result['category'] == category
        assert fields.items() <= result['observation'].items()
        assert 'private' not in output and 'secret' not in output


@pytest.mark.parametrize('protocol,manifest_digest,spec_digest,count', [
    ('mqtt', 'eb74d6283eb80ca116f52d69565f930eb05efb2854af5a3172a1ff57ac23014d',
     'a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09', 20),
    ('http', 'f2d8323fb82739c6deda347b7d156f71cfac3434928bb47a4b6e342efc24e91a',
     '31e090e0dd6cd7e82a7d981b08f3fd0005a08c2143d50c556a75eba031d6c4c3', 12),
])
def test_baseline_manifest_req_timeout_and_spec_bytes_unchanged(protocol, manifest_digest, spec_digest, count):
    import hashlib
    base = ROOT / 'gold_file' / protocol
    manifest = (base / 'acceptance.json').read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == manifest_digest
    assert hashlib.sha256((base / 'specIR.json').read_bytes()).hexdigest() == spec_digest
    assert len(json.loads(manifest)['checks']) == count


def test_mqtt_mandatory_boundaries_and_malformed_vectors(monkeypatch):
    _, net = simulate(monkeypatch, 'mqtt', 'length_boundaries', SEED)
    assert [len(body) for header, body in net.packets if header == 0x30] == [127, 128, 16383, 16384]
    _, net = simulate(monkeypatch, 'mqtt', 'pubsub', SEED)
    bodies = [body[2 + int.from_bytes(body[:2], 'big'):] for header, body in net.packets if header == 0x30]
    assert bodies[:2] == [b'', b'\x00\xff\xc0\x00binary']
    assert 2 <= len(bodies[2:-1]) <= 4
    _, net = simulate(monkeypatch, 'mqtt', 'qos', SEED)
    assert [body[-1] for header, body in net.packets if header == 0x82] == [2]
    retained = [body[2 + int.from_bytes(body[:2], 'big'):] for header, body in net.packets if header == 0x31]
    assert retained[:3] == [b'\x00\xffbinary'] * 3
    _, net = simulate(monkeypatch, 'mqtt', 'invalid_qos', SEED)
    assert [body[-1] for header, body in net.packets if header == 0x82] == [3, 4, 128]
    _, net = simulate(monkeypatch, 'mqtt', 'invalid_flags', SEED)
    assert [header for header, _ in net.packets if header not in (0x10, 0xc0)] == [0xc1, 0x80, 0xe1]
    _, net = simulate(monkeypatch, 'mqtt', 'invalid_utf8', SEED)
    assert [body[2:2 + int.from_bytes(body[:2], 'big')] for header, body in net.packets if header == 0x30] == [b'\xc0\xaf', b'\xed\xa0\x80', b'\x00']


@pytest.mark.parametrize('protocol,case,connection,skip,forced', [
    ('mqtt', 'fragmented_connect', 1, 0, {1, 2, 3, 7}),
    ('mqtt', 'fragmented_publish', 2, 1, {1, 2, 3, 4, 5, 8}),
    ('http', 'fragmented', 1, 0, {1, 5, 22}),
])
def test_forced_fragment_cuts_and_quiet_after_each_prefix(monkeypatch, protocol, case, connection, skip, forced):
    events, _ = simulate(monkeypatch, protocol, case, SEED)
    writes = [e for e in events if e['event'] == 'write' and e['connection'] == connection][skip:]
    if protocol == 'mqtt':
        writes = writes[:-1]  # final PINGREQ follows the completed CONNECT/PUBLISH
    ends, total = set(), 0
    for write in writes:
        total += write['requested']
        ends.add(total)
    assert forced | {total - 1, total} <= ends
    if protocol == 'http':
        assert total - len(b'body\x00\xff') - 1 in ends
    quiets = [e for e in events if e['event'] == 'quiet']
    assert len(quiets) == (len(writes) - 1) * (2 if case == 'fragmented_publish' else 1)
    assert all(e['outcome'] == 'quiet' for e in quiets)


@pytest.mark.parametrize('case', ['echo', 'length_errors'])
def test_http_original_binary_empty_large_and_invalid_lengths(monkeypatch, case):
    events, _ = simulate(monkeypatch, 'http', case, SEED)
    writes = [bytes.fromhex(e['data_hex']) for e in events if e['event'] == 'send']
    if case == 'echo':
        bodies = [w.split(b'\r\n\r\n', 1)[1] for w in writes]
        assert bodies[:3] == [b'', b'\x00\xff\r\n\r\nGET / HTTP/1.1\r\nbinary', b'z' * 16384]
        assert 2 <= len(bodies[3:]) <= 4
    else:
        invalid = [b'-1', b'abc', b'2x', b'2\r\nContent-Length: 3', b'2, 3',
                   b'2\r\nContent-Length: 2', b'2, 2', b'999999999999999999999999999999999999']
        assert writes[:8] == [b'POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: ' + length + b'\r\n\r\nabc' for length in invalid]


@pytest.mark.parametrize('protocol', ['mqtt', 'http'])
def test_main_consumes_env_and_emits_one_safe_record(monkeypatch, tmp_path, capsys, protocol):
    module = oracle(protocol)
    clock = Clock()
    network = Network(module, protocol, clock)
    monkeypatch.setattr(module, 'time', clock)
    monkeypatch.setattr(module.socket, 'create_connection', network.connect)
    monkeypatch.setenv('NEPA_ORACLE_SEED', SEED)
    path = tmp_path / 'host-only.jsonl'
    monkeypatch.setenv('NEPA_ORACLE_TRACE_FILE', str(path))
    case = 'pubsub' if protocol == 'mqtt' else 'echo'
    monkeypatch.setattr(sys, 'argv', ['oracle', '--host', 'localhost', '--port', '49123', '--case', case])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert json.loads(output) == {'passed': True, 'category': 'protocol', 'observation': {'outcome': 'accepted'}}
    assert json.loads(path.read_text().splitlines()[0])['seed'] == SEED
    assert SEED not in output and str(path) not in output


@pytest.mark.parametrize('protocol', ['mqtt', 'http'])
def test_send_failure_keeps_only_acknowledged_prefix(tmp_path, protocol):
    module = oracle(protocol)
    class FailingSocket:
        def __init__(self):
            self.calls = 0
        def send(self, data):
            self.calls += 1
            if self.calls == 1:
                return 2
            raise socket.timeout()
    path = tmp_path / 'partial.jsonl'
    sock = module.TraceSocket(FailingSocket(), module.Oracle(SEED, path), 3)
    with pytest.raises(socket.timeout):
        sock.sendall(b'abcdef')
    sends = [r for line in path.read_text().splitlines() if (r := json.loads(line))['event'] == 'send']
    assert sends[0]['data_hex'] == b'ab'.hex() and sends[0]['count'] == 2
    assert sends[1]['outcome'] == 'timeout' and sends[1]['count'] == 0 and sends[1]['offset'] == 2
    assert 'data_hex' not in sends[1]
