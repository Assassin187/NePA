"""Trusted HTTP fixed-length subset oracle. No generated server code is imported."""
from __future__ import annotations
import argparse
import contextlib
from functools import partial
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


def request(method='GET', path='/', body=b'', headers=None, *, oracle=None):
    host = oracle.token() + '.localhost' if oracle is not None else 'localhost'
    fields = [('Host', host), *(headers or [])]
    if body or method == 'POST':
        fields.append(('Content-Length', str(len(body))))
    return (f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields) + '\r\n').encode('ascii') + body


class Client:
    def __init__(self, host, port, oracle=None):
        self.oracle = oracle if oracle is not None else Oracle.from_env()
        self.sock = self.oracle.connect(host, port)
        self.buffer = b''
        self.order = 0

    def close(self):
        self.sock.close()

    def read(self, count, outcome, expected_length=None):
        try:
            return self.sock.recv(count)
        except socket.timeout:
            raise WireMismatch('receive_timeout', expected_outcome=outcome, actual_outcome='timeout',
                               expected_length=expected_length, actual_length=len(self.buffer),
                               actual_order=self.order) from None

    def response(self, *, head=False):
        self.order += 1
        while b'\r\n\r\n' not in self.buffer:
            part = self.read(4096, 'complete_headers')
            require(bool(part), 'header_EOF', expected_outcome='complete_headers',
                    actual_outcome='eof', actual_length=len(self.buffer), actual_order=self.order)
            self.buffer += part
            require(len(self.buffer) <= 131072, 'header_size', expected_length=131072, actual_length=len(self.buffer))
        header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        lines = header.split(b'\r\n')
        status_line = lines.pop(0).split(b' ', 2)
        require(len(status_line) == 3 and status_line[0] == b'HTTP/1.1', 'status_line',
                expected_outcome='http_1_1_status_line', actual_outcome='invalid', actual_length=len(header.split(b'\r\n', 1)[0]),
                actual_order=self.order)
        require(len(status_line[1]) == 3 and status_line[1].isdigit(), 'status_code',
                expected_length=3, actual_length=len(status_line[1]), actual_outcome='invalid_digits')
        fields = {}
        for index, line in enumerate(lines):
            require(b':' in line and not line.startswith((b' ', b'\t')), 'field_syntax',
                    expected_outcome='header_field', actual_outcome='invalid', offset=index, actual_length=len(line))
            name, value = line.split(b':', 1)
            name = name.lower()
            if name == b'content-length':
                require(name not in fields, 'duplicate_length', expected_length=1, actual_length=2)
            fields[name] = value.strip(b' \t')
        require(b'transfer-encoding' not in fields, 'transfer_encoding',
                expected_outcome='fixed_length', actual_outcome='transfer_encoding')
        length = fields.get(b'content-length', b'')
        require(bool(length) and length.isdigit(), 'content_length', expected_outcome='decimal_length',
                actual_outcome='invalid' if length else 'missing', actual_length=len(length))
        count = 0 if head else int(length)
        require(count <= 1024 * 1024, 'body_size', expected_length=1024 * 1024, actual_length=count)
        while len(self.buffer) < count:
            part = self.read(min(65536, count - len(self.buffer)), 'complete_body', count)
            require(bool(part), 'body_EOF', expected_length=count, actual_length=len(self.buffer),
                    expected_outcome='complete_body', actual_outcome='eof', actual_order=self.order)
            self.buffer += part
        body, self.buffer = self.buffer[:count], self.buffer[count:]
        return int(status_line[1]), fields, body

    def expect(self, status, body=None, *, head=False):
        try:
            actual, headers, payload = self.response(head=head)
        except WireMismatch as exc:
            exc.observation['expected_status'] = status
            raise
        require(actual == status, 'response_status', expected_status=status, actual_status=actual, actual_order=self.order)
        if body is not None:
            try:
                equal_bytes(payload, body, 'response_body')
            except WireMismatch as exc:
                exc.observation.update(expected_status=status, actual_status=actual, actual_order=self.order)
                raise
        return headers

    def quiet(self, seconds=.08):
        if self.buffer:
            self.sock.observe('quiet', seconds=seconds, outcome='buffered_data', count=len(self.buffer))
        require(not self.buffer, 'quiet', expected_outcome='open_quiet', actual_outcome='buffered_data', actual_length=len(self.buffer))
        self.sock.settimeout(seconds)
        try:
            part = self.sock.recv(1)
        except socket.timeout:
            self.sock.observe('quiet', seconds=seconds, outcome='quiet')
            return
        finally:
            self.sock.settimeout(2)
        self.sock.observe('quiet', seconds=seconds, outcome='data' if part else 'eof')
        raise WireMismatch('quiet', expected_outcome='open_quiet',
                           actual_outcome='data' if part else 'eof', actual_length=len(part))

    def eof(self):
        require(not self.buffer, 'connection_close', expected_outcome='eof',
                actual_outcome='buffered_data', actual_length=len(self.buffer))
        try:
            data = self.sock.recv(1)
        except socket.timeout:
            raise WireMismatch('connection_close', expected_outcome='eof', actual_outcome='timeout') from None
        require(data == b'', 'connection_close', expected_outcome='eof', actual_outcome='data', actual_length=len(data))


def exercise(case, host, port, oracle=None):
    oracle = oracle if oracle is not None else Oracle.from_env()
    make_request = partial(request, oracle=oracle)
    with contextlib.ExitStack() as stack:
        def client():
            value = Client(host, port, oracle=oracle)
            stack.callback(value.close)
            return value
        c = None if case in ('routes', 'host_errors', 'length_errors', 'malformed', 'transfer_encoding') else client()
        if case == 'get_head':
            c.sock.sendall(make_request())
            headers = c.expect(200, b'nepa\n')
            c.sock.sendall(make_request('HEAD') + make_request())
            head = c.expect(200, b'', head=True)
            require(head[b'content-length'] == headers[b'content-length'] == b'5', 'head_length',
                    expected_length=5, actual_length=int(head[b'content-length']),
                    actual_outcome='metadata_mismatch')
            c.expect(200, b'nepa\n')
        elif case == 'echo':
            for body in (b'', b'\x00\xff\r\n\r\nGET / HTTP/1.1\r\nbinary', b'z' * 16384,
                         *(oracle.payload() for _ in range(oracle.rng.randint(2, 4)))):
                c.sock.sendall(make_request('POST', '/echo', body))
                c.expect(200, body)
        elif case == 'routes':
            for method, path, status in [('GET', '/missing', 404), ('POST', '/missing', 404), ('HEAD', '/missing', 404), ('BREW', '/', 501), ('get', '/', 501)]:
                value = client()
                value.sock.sendall(make_request(method, path))
                headers = value.expect(status, b'', head=method == 'HEAD')
                require(headers[b'content-length'] == b'0', 'route_length', expected_length=0, actual_length=int(headers[b'content-length']))
                value.close()
            c = client()
            for _ in range(oracle.rng.randint(2, 4)):
                method = oracle.rng.choice(('GET', 'POST', 'HEAD'))
                c.sock.sendall(make_request(method, '/missing' + oracle.token()))
                headers = c.expect(404, b'', head=method == 'HEAD')
                require(headers[b'content-length'] == b'0', 'route_length', expected_length=0, actual_length=int(headers[b'content-length']))
        elif case == 'header_case':
            c.sock.sendall(b'POST /echo HTTP/1.1\r\nhOsT: localhost\r\ncOnTeNt-LeNgTh:\t3 \t\r\nX-Extra: yes\r\n\r\nabc')
            c.expect(200, b'abc')
            for _ in range(oracle.rng.randint(2, 4)):
                body = oracle.payload()
                fields = [('Host', 'localhost'), ('Content-Length', str(len(body))), ('X-Extra', oracle.token())]
                fields = [(''.join(oracle.rng.choice((ch.lower(), ch.upper())) for ch in name), value)
                          for name, value in fields]
                packet = ('POST /echo HTTP/1.1\r\n' + ''.join(f'{k}:\t{v} \t\r\n' for k, v in fields) + '\r\n').encode('ascii') + body
                c.sock.sendall(packet)
                c.expect(200, body)
        elif case == 'host_errors':
            for fields in (b'', b'Host: a\r\nHost: b\r\n', b'Host: bad host\r\n'):
                value = client()
                value.sock.sendall(b'GET / HTTP/1.1\r\n' + fields + b'\r\n')
                value.expect(400)
                value.eof()
        elif case == 'length_errors':
            for length in (b'-1', b'abc', b'2x', b'2\r\nContent-Length: 3', b'2, 3', b'2\r\nContent-Length: 2', b'2, 2', b'999999999999999999999999999999999999'):
                value = client()
                value.sock.sendall(b'POST /echo HTTP/1.1\r\nHost: localhost\r\nContent-Length: ' + length + b'\r\n\r\nabc')
                value.expect(400)
                value.eof()
        elif case == 'fragmented':
            body = b'body\x00\xff'
            packet = make_request('POST', '/echo', body)
            start = 0
            for end in oracle.cuts(len(packet), (1, 5, 22, len(packet) - len(body) - 1, len(packet) - 1, len(packet))):
                c.sock.sendall(packet[start:end])
                if end < len(packet):
                    c.quiet()
                start = end
            c.expect(200, body)
        elif case == 'pipeline':
            tail = make_request('POST', '/echo', b'two')
            c.sock.sendall(make_request() + make_request('POST', '/echo', b'one') + tail[:-1])
            c.expect(200, b'nepa\n')
            c.expect(200, b'one')
            c.quiet()
            c.sock.sendall(tail[-1:])
            c.expect(200, b'two')
            bodies = [oracle.payload() for _ in range(oracle.rng.randint(2, 4))]
            start = 0
            while start < len(bodies):
                group = bodies[start:start + oracle.rng.randint(2, 4)]
                c.sock.sendall(b''.join(make_request('POST', '/echo', body) for body in group))
                for body in group:
                    c.expect(200, body)
                start += len(group)
        elif case == 'connection_close':
            c.sock.sendall(make_request(headers=[('Connection', 'close')]))
            headers = c.expect(200, b'nepa\n')
            require(headers.get(b'connection', b'').lower() == b'close', 'close_header',
                    expected_outcome='close', actual_outcome='missing_or_invalid')
            c.eof()
        elif case == 'truncated':
            c.sock.sendall(make_request('POST', '/echo', b'body')[:-1])
            c.quiet()
            c.sock.shutdown(socket.SHUT_WR)
            c.eof()
            good = client()
            good.sock.sendall(make_request())
            good.expect(200, b'nepa\n')
        elif case == 'malformed':
            for raw in (b'GET /\r\nHost: localhost\r\n\r\n', b'GET / HTTP/1.1\nHost: localhost\r\n\r\n', b'GET / HTTP/1.1\r\nHost : localhost\r\n\r\n', b'GET / HTTP/1.1\r\nBadHeader\r\nHost: localhost\r\n\r\n'):
                value = client()
                value.sock.sendall(raw)
                value.expect(400)
                value.eof()
        elif case == 'transfer_encoding':
            for suffix in (b'', b'Content-Length: 5\r\n'):
                value = client()
                value.sock.sendall(b'POST /echo HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n' + suffix + b'\r\n0\r\n\r\n')
                value.expect(400)
                value.eof()
        else:
            raise ValueError(f'unknown case: {case}')
        if case in ('host_errors', 'length_errors', 'malformed', 'transfer_encoding'):
            # Keep every fixed rejection vector, then vary independent healthy traffic.
            c = client()
            for _ in range(oracle.rng.randint(2, 4)):
                body = oracle.payload()
                c.sock.sendall(make_request('POST', '/echo', body))
                c.expect(200, body)


CASES = {
    'get_head': ['REQ-HTTP-APP-GET-001', 'REQ-HTTP-APP-HEAD-001', 'REQ-HTTP-RESPONSE-001', 'REQ-HTTP-HEAD-001', 'REQ-HTTP-RESPONSE-LENGTH-001', 'REQ-HTTP-PERSISTENCE-001'],
    'echo': ['REQ-HTTP-APP-ECHO-001', 'REQ-HTTP-BODY-001', 'REQ-HTTP-RESPONSE-LENGTH-001'],
    'routes': ['REQ-HTTP-APP-ROUTES-001', 'REQ-HTTP-APP-METHODS-001'],
    'header_case': ['REQ-HTTP-FIELD-NAMES-001', 'REQ-HTTP-OWS-001', 'REQ-HTTP-BODY-001'],
    'host_errors': ['REQ-HTTP-HOST-001'],
    'length_errors': ['REQ-HTTP-LENGTH-INVALID-001', 'REQ-HTTP-LENGTH-CONFLICT-001'],
    'fragmented': ['REQ-HTTP-STREAM-001', 'REQ-HTTP-INCOMPLETE-WAIT-001', 'REQ-HTTP-BODY-001'],
    'pipeline': ['REQ-HTTP-STREAM-001', 'REQ-HTTP-PERSISTENCE-001', 'REQ-HTTP-PIPELINE-001', 'REQ-HTTP-INCOMPLETE-WAIT-001'],
    'connection_close': ['REQ-HTTP-CLOSE-001'],
    'truncated': ['REQ-HTTP-TRUNCATED-001', 'REQ-HTTP-ISOLATION-001'],
    'malformed': ['REQ-HTTP-CRLF-001', 'REQ-HTTP-REQUEST-LINE-001', 'REQ-HTTP-FIELD-SYNTAX-001', 'REQ-HTTP-BAD-REQUEST-001'],
    'transfer_encoding': ['REQ-HTTP-SUBSET-TRANSFER-001'],
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
