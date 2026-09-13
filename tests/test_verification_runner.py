"""Known-response doubles validate the oracle, never count as generated protocols."""
import json
from pathlib import Path
import pytest
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner

ROOT = Path(__file__).parents[1]
SERVER = """
import signal, socket, sys
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
mode = sys.argv[3]
if mode == 'sanitizer':
    print('AddressSanitizer: test-only diagnostic', file=sys.stderr, flush=True)
def read(s, n):
    result = b''
    while len(result) < n:
        chunk = s.recv(n-len(result))
        if not chunk:
            return result
        result += chunk
    return result
with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((sys.argv[1], int(sys.argv[2])))
    server.listen()
    while True:
        with server.accept()[0] as s:
            header = read(s, 2)
            if len(header) != 2:
                continue
            body = read(s, header[1])
            level = body[6]
            reply = bytes([32,2,0,0 if level == 4 else 1])
            if mode == 'wrong':
                reply = bytes([32,2,0,5])
            s.sendall(reply)
            if level != 4:
                continue
            while read(s, 2) == bytes([192,0]):
                s.sendall(bytes([208,0]))
"""

@pytest.mark.sandbox_integration
@pytest.mark.parametrize("mode,expected", [("correct", True), ("wrong", False), ("sanitizer", False)])
def test_independent_oracle_correct_and_wrong_responses(tmp_path, mode, expected):
    project = tmp_path / "project"
    project.mkdir()
    (project / "server.py").write_text(SERVER)
    target = {"builds": [{"id": "release", "artifact": "server.py"}, {"id": "san", "artifact": "server.py"}],
              "run": ["python", "{artifact}", "{host}", "{port}", mode]}
    acceptance = json.loads((ROOT / "gold_file/mqtt/acceptance.json").read_bytes())
    acceptance["checks"] = acceptance["checks"][:1]  # This supervisor double implements only the minimum oracle.
    runner = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    result = runner.run(target, acceptance, project, ROOT / "gold_file/mqtt", tmp_path / "evidence")
    assert result["passed"] is expected, result
    if mode == "sanitizer":
        assert all(v["detail"]["sanitizer_error"] for v in result["variants"])

@pytest.mark.sandbox_integration
def test_missing_binary_and_idle_server_fail(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runner = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    acceptance = json.loads((ROOT / "gold_file/mqtt/acceptance.json").read_bytes())
    acceptance["checks"] = acceptance["checks"][:1]  # This supervisor double implements only the minimum oracle.
    target = {"builds": [{"id": "release", "artifact": "missing"}], "run": ["{artifact}"]}
    assert not runner.run(target, acceptance, project, ROOT / "gold_file/mqtt", tmp_path / "missing")["passed"]
    (project / "idle.py").write_text("import time; time.sleep(60)")
    target = {"builds": [{"id": "release", "artifact": "idle.py"}], "run": ["python", "{artifact}"]}
    acceptance["checks"][0]["timeout_s"] = 1
    assert not runner.run(target, acceptance, project, ROOT / "gold_file/mqtt", tmp_path / "idle")["passed"]


@pytest.mark.sandbox_integration
def test_private_seed_replays_actual_wire_bytes_and_varies_independently(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    (project / "server.py").write_text(SERVER)
    target = {"builds": [{"id": "release", "artifact": "server.py"}, {"id": "san", "artifact": "server.py"}],
              "run": ["python", "{artifact}", "{host}", "{port}", "correct"]}
    acceptance = json.loads((ROOT / "gold_file/mqtt/acceptance.json").read_bytes())
    acceptance["checks"] = acceptance["checks"][:1]
    runner = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    wires, ports = [], []
    for number, seed in enumerate(("replay-a", "replay-a", "replay-b")):
        directory = tmp_path / f"attempt-{number}"
        result = runner.run(target, acceptance, project, ROOT / "gold_file/mqtt", directory, seed=seed)
        assert result["passed"], result
        attempt = {}
        for variant in ("release", "san"):
            events = [json.loads(line) for line in (directory / variant / "checker/trace-0000.jsonl").read_text().splitlines()]
            # Actual sent byte stream per connection, independent of kernel chunking/timestamps.
            connections = {}
            for event in events:
                if event["event"] == "send" and event.get("count"):
                    connections.setdefault(event["connection"], []).append(event["data_hex"])
            attempt[variant] = {connection: ''.join(data) for connection, data in connections.items()}
        wires.append(attempt)
        ports.append([variant["detail"]["port"] for variant in result["variants"]])
    assert wires[0] == wires[1] and wires[0] != wires[2]
    assert wires[0]["release"] != wires[0]["san"]
    assert ports[0] == ports[1] and len(set(ports[0])) == 2
