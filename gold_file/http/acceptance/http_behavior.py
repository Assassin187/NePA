"""Trusted HTTP fixed-length subset oracle. No generated server code is imported."""
from __future__ import annotations
import argparse
import contextlib
import json
import socket
import time


def request(method='GET', path='/', body=b'', headers=None):
    fields = [('Host', 'localhost'), *(headers or [])]
    if body or method == 'POST':
        fields.append(('Content-Length', str(len(body))))
    return (f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields) + '\r\n').encode('ascii') + body


class Client:
    def __init__(self, host, port):
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
        self.buffer = b''

    def close(self):
        self.sock.close()

    def response(self, *, head=False):
        while b'\r\n\r\n' not in self.buffer:
            part = self.sock.recv(4096)
            assert part, 'EOF inside response headers'
            self.buffer += part
            assert len(self.buffer) <= 131072, 'unbounded response header'
        header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        lines = header.split(b'\r\n')
        status_line = lines.pop(0).split(b' ', 2)
        assert len(status_line) == 3 and status_line[0] == b'HTTP/1.1', f'invalid status line: {status_line!r}'
        assert len(status_line[1]) == 3 and status_line[1].isdigit()
        fields = {}
        for line in lines:
            assert b':' in line and not line.startswith((b' ', b'\t')), f'invalid field: {line!r}'
            name, value = line.split(b':', 1)
            name = name.lower()
            if name == b'content-length':
                assert name not in fields, 'duplicate response length'
            fields[name] = value.strip(b' \t')
        assert b'transfer-encoding' not in fields, 'response outside fixed-length subset'
        length = fields.get(b'content-length', b'')
        assert length and length.isdigit(), 'missing/invalid response Content-Length'
        count = 0 if head else int(length)
        assert count <= 1024 * 1024, 'unexpected response body size'
        while len(self.buffer) < count:
            part = self.sock.recv(min(65536, count - len(self.buffer)))
            assert part, 'EOF inside fixed-length response body'
            self.buffer += part
        body, self.buffer = self.buffer[:count], self.buffer[count:]
        return int(status_line[1]), fields, body

    def expect(self, status, body=None, *, head=False):
        actual, headers, payload = self.response(head=head)
        assert actual == status, f'expected status {status}, got {actual}'
        if body is not None:
            assert payload == body, f'expected body {body!r}, got {payload!r}'
        return headers

    def quiet(self, seconds=.08):
        assert not self.buffer, f'premature response bytes: {self.buffer!r}'
        self.sock.settimeout(seconds)
        try:
            part = self.sock.recv(1)
        except socket.timeout:
            return
        finally:
            self.sock.settimeout(2)
        raise AssertionError(f'expected open quiet connection, got {part!r}')

    def eof(self):
        assert not self.buffer, 'unexpected trailing response data'
        assert self.sock.recv(1) == b'', 'expected closed connection'


def exercise(case, host, port):
    with contextlib.ExitStack() as stack:
        def client():
            value = Client(host, port)
            stack.callback(value.close)
            return value
        c = client()
        if case == 'get_head':
            c.sock.sendall(request())
            headers = c.expect(200, b'nepa\n')
            c.sock.sendall(request('HEAD') + request())
            head = c.expect(200, b'', head=True)
            assert head[b'content-length'] == headers[b'content-length'] == b'5'
            c.expect(200, b'nepa\n')
        elif case == 'echo':
            for body in (b'', b'\x00\xff\r\n\r\nGET / HTTP/1.1\r\nbinary', b'z' * 16384):
                c.sock.sendall(request('POST', '/echo', body))
                c.expect(200, body)
        elif case == 'routes':
            for method, path, status in [('GET', '/missing', 404), ('POST', '/missing', 404), ('HEAD', '/missing', 404), ('BREW', '/', 501), ('get', '/', 501)]:
                value = client()
                value.sock.sendall(request(method, path))
                headers = value.expect(status, b'', head=method == 'HEAD')
                assert headers[b'content-length'] == b'0'
        elif case == 'header_case':
            c.sock.sendall(b'POST /echo HTTP/1.1\r\nhOsT: localhost\r\ncOnTeNt-LeNgTh:\t3 \t\r\nX-Extra: yes\r\n\r\nabc')
            c.expect(200, b'abc')
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
            packet = request('POST', '/echo', body)
            start = 0
            for end in (1, 5, 22, len(packet) - len(body) - 1, len(packet) - 1, len(packet)):
                c.sock.sendall(packet[start:end])
                if end < len(packet):
                    c.quiet()
                start = end
            c.expect(200, body)
        elif case == 'pipeline':
            tail = request('POST', '/echo', b'two')
            c.sock.sendall(request() + request('POST', '/echo', b'one') + tail[:-1])
            c.expect(200, b'nepa\n')
            c.expect(200, b'one')
            c.quiet()
            c.sock.sendall(tail[-1:])
            c.expect(200, b'two')
        elif case == 'connection_close':
            c.sock.sendall(request(headers=[('Connection', 'close')]))
            headers = c.expect(200, b'nepa\n')
            assert headers.get(b'connection', b'').lower() == b'close'
            c.eof()
        elif case == 'truncated':
            c.sock.sendall(request('POST', '/echo', b'body')[:-1])
            c.quiet()
            c.sock.shutdown(socket.SHUT_WR)
            c.eof()
            good = client()
            good.sock.sendall(request())
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
    try:
        exercise(args.case, args.host, args.port)
        print(json.dumps({'case': args.case, 'passed': True}))
        return 0
    except (AssertionError, ValueError, OSError) as exc:
        print(json.dumps({'case': args.case, 'passed': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
