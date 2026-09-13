"""Host-private controlled echo assertion. Never import generated source."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import socket
import time


def check(host, port, seed, trace_file):
    rng = random.Random(int(seed, 16))
    payload = b'\x00\xff' + bytes(rng.randrange(256) for _ in range(rng.randrange(64, 193)))
    def trace(event, **fields):
        with Path(trace_file).open('a') as stream:
            stream.write(json.dumps({'event': event, 'at': time.time(), **fields}) + '\n')
    trace('start', seed=seed, randomization_version='controlled-echo/1')
    received = bytearray()
    with socket.create_connection((host, port), timeout=2) as connection:
        trace('send', data_hex=payload.hex(), length=len(payload))
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        trace('half_close')
        while True:
            data = connection.recv(4096)
            trace('recv', data_hex=data.hex(), length=len(data))
            if not data:
                break
            received.extend(data)
    passed = bytes(received) == payload
    return {'passed': passed, 'category': 'echo_exact' if passed else 'echo_mismatch',
            'observation': {'expected_length': len(payload), 'actual_length': len(received),
                            'expected_outcome': 'complete_echo',
                            'actual_outcome': 'complete_echo' if passed else 'different_echo'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    result = check(args.host, args.port, os.environ['NEPA_ORACLE_SEED'], os.environ['NEPA_ORACLE_TRACE_FILE'])
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
