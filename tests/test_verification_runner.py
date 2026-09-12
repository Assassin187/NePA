"""Known-response doubles validate the oracle, never count as generated protocols."""
import json
from pathlib import Path
import pytest
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner
from nepa.tools.verification_worker import supervise

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
            if read(s, 2) == bytes([192,0]):
                s.sendall(bytes([208,0]))
            read(s, 2)
"""

@pytest.mark.sandbox_integration
@pytest.mark.parametrize("mode,expected", [("correct", True), ("wrong", False), ("sanitizer", False)])
def test_independent_oracle_correct_and_wrong_responses(tmp_path, mode, expected):
    project = tmp_path / "project"
    project.mkdir()
    (project / "server.py").write_text(SERVER)
    target = {"builds": [{"id": "release", "artifact": "server.py"}, {"id": "san", "artifact": "server.py"}],
              "run": ["python", "{artifact}", "{host}", "{port}", mode]}
    acceptance = json.loads((ROOT / "gold_file/acceptance.json").read_bytes())
    runner = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    result = runner.run(target, acceptance, project, ROOT / "gold_file", tmp_path / "evidence")
    assert result["passed"] is expected, result
    if mode == "sanitizer":
        assert all(v["detail"]["sanitizer_error"] for v in result["variants"])

@pytest.mark.sandbox_integration
def test_missing_binary_and_idle_server_fail(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runner = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1))
    acceptance = json.loads((ROOT / "gold_file/acceptance.json").read_bytes())
    target = {"builds": [{"id": "release", "artifact": "missing"}], "run": ["{artifact}"]}
    assert not runner.run(target, acceptance, project, ROOT / "gold_file", tmp_path / "missing")["passed"]
    (project / "idle.py").write_text("import time; time.sleep(60)")
    target = {"builds": [{"id": "release", "artifact": "idle.py"}], "run": ["python", "{artifact}"]}
    acceptance["checks"][0]["timeout_s"] = 1
    assert not runner.run(target, acceptance, project, ROOT / "gold_file", tmp_path / "idle")["passed"]
