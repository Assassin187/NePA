"""Deliberately wrong wire fixtures verify assertions, never generation success."""
import importlib.util
from pathlib import Path
import socket
import pytest
from nepa.speclib.lint import lint_acceptance, lint_spec, lint_target
from nepa.speclib.plan import compile_plan

ROOT = Path(__file__).parents[1]


def oracle(protocol):
    path = ROOT / f'gold_file/{protocol}/acceptance/{protocol}_behavior.py'
    spec = importlib.util.spec_from_file_location(protocol + '_oracle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WireSocket:
    def __init__(self, data=b'', *, idle=False):
        self.data, self.idle = data, idle
    def recv(self, count):
        if not self.data and self.idle:
            raise socket.timeout()
        # Deliberately short reads exercise the oracle's own stream handling.
        value, self.data = self.data[:min(count, 3)], self.data[min(count, 3):]
        return value
    def settimeout(self, timeout):
        pass
    def sendall(self, data):
        pass
    def observe(self, event, **fields):
        pass


def client(module, data=b'', idle=False):
    c = module.Client.__new__(module.Client)
    c.sock = WireSocket(data, idle=idle)
    c.buffer = b''
    c.order = 0
    c.oracle = module.Oracle('01' * 32)
    return c


def test_mqtt_independent_vectors_and_dropped_forwarding():
    m = oracle('mqtt')
    assert m.connect_packet('x') == bytes.fromhex('100d00044d5154540402001e000178')
    assert m.remaining(127) == b'\x7f' and m.remaining(128) == b'\x80\x01'
    assert m.remaining(16384) == b'\x80\x80\x01'
    wire = m.frame(0x30, m.string('topic') + b'\x00\xff')
    client(m, wire).delivered('topic', b'\x00\xff')
    with pytest.raises(AssertionError, match='EOF'):
        client(m).delivered('topic')
    with pytest.raises(AssertionError, match='expected'):
        client(m, m.frame(0x38, m.string('topic') + b'hello')).delivered('topic')


def test_mqtt_wrong_suback_and_unsuback_are_detected():
    m = oracle('mqtt')
    client(m, m.frame(0x90, b'\x12\x34\x00')).subscribe([('topic', 0)], identifier=0x1234)
    for body in (b'\x12\x35\x00', b'\x12\x34', b'\x12\x34\x03'):
        with pytest.raises(AssertionError):
            client(m, m.frame(0x90, body)).subscribe([('topic', 0)], identifier=0x1234)
    with pytest.raises(AssertionError):
        client(m, m.frame(0xb0, b'\x00\x01')).unsubscribe(['topic'], identifier=0x4567)


def test_quiet_checks_detect_leaked_session_and_early_partial_reply():
    m, h = oracle('mqtt'), oracle('http')
    client(m, idle=True).quiet()
    client(h, idle=True).quiet()
    with pytest.raises(AssertionError):
        client(m, m.frame(0x30, m.string('old-session') + b'leaked')).quiet()
    with pytest.raises(AssertionError):
        client(h, b'HTTP/1.1 200 OK\r\n').quiet()


def test_http_length_head_and_binary_response_validation():
    h = oracle('http')
    body = b'\x00\xff\r\n'
    wire = b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n' + body
    client(h, wire).expect(200, body)
    for bad in (wire.replace(b'Length: 4', b'Length: 5'), wire.replace(b'Length: 4', b'Length: 3'), wire.replace(b'200 OK', b'404 Missing')):
        with pytest.raises(AssertionError):
            client(h, bad).expect(200, body)
    c = client(h, b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n' + wire)
    c.expect(200, b'', head=True)
    c.expect(200, body)
    wrong = client(h, b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nWRNG' + wire)
    wrong.expect(200, b'', head=True)
    with pytest.raises(AssertionError):
        wrong.expect(200, body)


@pytest.mark.parametrize('protocol', ['mqtt', 'http'])
def test_parallel_manual_inputs_lint_and_deterministic_plan(protocol):
    root = ROOT / 'gold_file' / protocol
    assert lint_spec(root / 'specIR.json')['valid']
    assert lint_target(root / 'target.json', root / 'specIR.json')['valid']
    assert lint_acceptance(root / 'acceptance.json', root / 'specIR.json')['valid']
    import json
    spec = json.loads((root / 'specIR.json').read_text())
    target = json.loads((root / 'target.json').read_text())
    plan = compile_plan(spec, target)
    assert plan == compile_plan(spec, target)
    assert list(plan['primary_tasks']) == [r['id'] for r in spec['requirements']]
    assert (root / 'target.json').read_bytes() == (ROOT / 'gold_file/mqtt/target.json').read_bytes()
