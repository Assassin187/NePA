"""Private-verifier controls use copied exports and offline fixtures, never paid calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from nepa.config import load_config
from nepa.report import public_report, publish_report
from nepa.run_store import RunStore, tree_hashes
from nepa.tools.build import BuildRunner
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner, safe_feedback
from nepa.tools.workspace import WorkspaceTools

ROOT = Path(__file__).parents[1]
BASELINES = {"mqtt": ROOT / "runs/mqtt-e2e/20260912T164202Z-e4b27709/delivery",
             "http": ROOT / "runs/http-e2e/20260912T164202Z-d0b839c4/delivery"}


def test_relative_sanitizer_frames_keep_only_existing_public_sources(tmp_path):
    from nepa.tools.verification import _source_locations
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/main.c').write_text('line1\nline2\n')
    text = ('#0 0x123 in handle src/main.c:2\n'
            '#1 0x124 in bad /private/src/main.c:1\n'
            '#2 0x125 in bad ../src/main.c:1\n'
            '#3 0x126 in bad src/main.c:99\n')
    assert _source_locations(text, tmp_path) == [{'path': 'src/main.c', 'line': 2}]


def test_large_feedback_keeps_late_variant_failures_and_locations():
    from nepa.agents.session import CodingSession
    ref = {'path': 'evidence/full.json', 'sha256': 'a' * 64}
    variants = [{'variant': name, 'detail': {'checks': [
        {'id': 'pass-' + str(i), 'passed': True, 'padding': 'x' * 500} for i in range(50)] +
        [{'id': 'broken', 'passed': False, 'category': 'memory'}],
        'source_locations': [{'path': 'src/main.c', 'line': 533}], 'sanitizer_error': name == 'san'}}
        for name in ('release', 'san')]
    value = {'accepted': False, 'build': {'passed': True}, 'verification': {'passed': False, 'variants': variants}}
    view = CodingSession._feedback_view(value, ref)['tool_result']
    assert view['complete_result_ref'] == ref
    for row in view['verification']['variants']:
        assert row['detail']['checks'] == [{'id': 'broken', 'passed': False, 'category': 'memory'}]
        assert row['detail']['source_locations'][0]['line'] == 533
    assert len(value['verification']['variants'][0]['detail']['checks']) == 51


def test_private_snapshot_raw_bytes_and_published_evidence(tmp_path):
    source = ROOT / "gold_file/mqtt"
    store = RunStore.initialize(tmp_path, source / "specIR.json", source / "target.json", source / "acceptance.json", load_config())
    assert (store.root / "private/acceptance.json").read_bytes() == (source / "acceptance.json").read_bytes()
    assert {p.name for p in (store.root / "inputs").iterdir()} == {"spec.json", "target.json", "index.json"}
    assert set(store.run["inputs"]) == {"spec", "target", "index"}
    for asset in store.inputs()[2]["assets"]:
        assert (store.private_checks / asset).read_bytes() == (source / asset).read_bytes()
    raw = store.evidence("private.json", {"secret": "PRIVATE_CANARY"})
    with pytest.raises(FileNotFoundError):
        store.read_agent_evidence(raw)
    ref = store.publish_agent_evidence("private.json", {"passed": False})
    assert store.read_agent_evidence(ref) == {"passed": False}
    assert store.read_ref(raw) == {"secret": "PRIVATE_CANARY"}
    with pytest.raises(FileExistsError):
        store.publish_agent_evidence("private.json", {})
    store.run.update(status="failed", exit_code=2, reason="test")
    host = publish_report(store)
    public = public_report(host)
    text = json.dumps(public)
    assert public["visibility"] == "public" and host["visibility"] == "host_only"
    for private in ("private/", "evidence/", "acceptance/", "PRIVATE_CANARY", "calls/"):
        assert private not in text


@pytest.mark.parametrize("tool,args", [
    ("read_file", {"path": "leak/secret"}), ("list_files", {}),
    ("search", {"pattern": "CANARY"}), ("write_file", {"path": "leak/secret", "content": "x"}),
    ("replace_text", {"path": "leak/secret", "old": "CANARY", "new": "x"}),
])
def test_every_host_file_tool_rejects_descendant_escape(tmp_path, tool, args):
    for name in ("project", "inputs", "agent-evidence", "private"):
        (tmp_path / name).mkdir()
    (tmp_path / "private/secret").write_text("PRIVATE_CANARY")
    (tmp_path / "project/leak").symlink_to(tmp_path / "private", target_is_directory=True)
    tools = WorkspaceTools(tmp_path / "project", tmp_path / "inputs", tmp_path / "agent-evidence", SandboxExecutor("unused", 1, 1))
    with pytest.raises(ValueError, match="escaped"):
        tools.execute(tool, args)
    with pytest.raises(ValueError, match="escaped"):
        tools.file_sha256("leak/secret")
    assert (tmp_path / "private/secret").read_text() == "PRIVATE_CANARY"


def test_recursive_feedback_omits_encoded_echoes_but_keeps_semantic_failure():
    secret = "PRIVATE_CANARY"
    row = {"id": "case", "passed": False, "category": "expected_packet_type", "argv": ["/checks/secret.py"],
           "stdout": secret, "stderr": secret.encode().hex(), "req_ids": [],
           "observation": {"expected_type": 144, "actual_type": 32, "actual_length": 4, "actual_order": 2,
                           "expected_outcome": "matching_bytes", "actual_outcome": "different_bytes",
                           "seed": secret, "raw": secret}}
    detail = {"passed": False, "checks": [row], "server_stdout": secret, "server_stderr": "UFJJVkFURV9DQU5BUlk=",
              "server_argv": ["/workspace/a"], "seed": secret, "sanitizer_error": True, "server_returncode": 1,
              "source_locations": [{"path": "src/a.c", "line": 17}]}
    raw = {"result": {"verification": {"passed": False, "variants": [{"variant": "san", "passed": False,
            "execution": {"stdout": secret}, "detail": detail}]}}, "evidence": {"path": "evidence/raw.json", "sha256": "a"*64}}
    view = safe_feedback(raw)
    text = json.dumps(view)
    for token in (secret, secret.encode().hex(), "UFJJVkFURV9DQU5BUlk=", "/checks", "raw.json", "seed"):
        assert token not in text
    observation = view["result"]["verification"]["variants"][0]["detail"]["checks"][0]["observation"]
    assert observation["actual_type"] == 32 and observation["expected_type"] == 144
    assert safe_feedback(view) == view
    assert secret not in json.dumps(safe_feedback({"nested": detail}))
    assert secret not in json.dumps(safe_feedback({"nested": row}))


@pytest.mark.sandbox_integration
@pytest.mark.parametrize("protocol,count,fault", [
    ("mqtt", 20, "correct"), ("http", 12, "correct"),
    ("mqtt", 20, "wrong_response"), ("http", 12, "wrong_response"),
    ("mqtt", 20, "single_id"), ("http", 12, "fixed_port"),
])
def test_complete_private_suite_on_copied_baseline(tmp_path, protocol, count, fault):
    baseline = BASELINES[protocol]
    if not baseline.is_dir():
        pytest.skip("historical local control export is not available")
    before = tree_hashes(baseline)
    project = tmp_path / "project"
    shutil.copytree(baseline, project, symlinks=True)
    source = ROOT / "gold_file" / protocol
    private = tmp_path / "private"
    private.mkdir()
    target = json.loads((source / "target.json").read_bytes())
    acceptance = json.loads((source / "acceptance.json").read_bytes())
    for asset in acceptance["assets"]:
        destination = private / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / asset, destination)
    if fault == "wrong_response":
        path = project / ("src/server.c" if protocol == "mqtt" else "src/http.c")
        old = "send_connack_message(c->fd, resumed != 0 ? 1U : 0U, 0x00U)" if protocol == "mqtt" else 'http_build_response(out, 200, "OK",'
        new = "send_connack_message(c->fd, resumed != 0 ? 1U : 0U, 0x05U)" if protocol == "mqtt" else 'http_build_response(out, 503, "Unavailable",'
        text = path.read_text()
        assert old in text
        path.write_text(text.replace(old, new))
    elif fault == "single_id":
        path = project / "src/server.c"
        text = path.read_text()
        old = "struct session *s = session_acquire(msg.client_id, msg.client_id_len,"
        assert old in text
        text = text.replace(old, 'if (msg.client_id_len != 13 || memcmp(msg.client_id, "only-fixed-id", 13) != 0) { (void)send_connack(c->fd, 2); return -1; }\n        ' + old)
        path.write_text(text)
    elif fault == "fixed_port":
        target["run"] = ["1883" if arg == "{port}" else arg for arg in target["run"]]
    executor = SandboxExecutor("nepa-sandbox:refactor", 2, 2)
    build = BuildRunner(executor).run(target, project, clean=True)
    assert build["passed"], build
    result = VerificationRunner(executor).run(target, acceptance, project, private, tmp_path / "verification", seed="offline-control-seed")
    assert result["passed"] is (fault == "correct"), result
    assert [len(v["detail"]["checks"]) for v in result["variants"]] == [count, count]
    for record in (tmp_path / "verification").rglob("containers.json"):
        state = json.loads(record.read_bytes())
        assert state["cleaned"]
        assert subprocess.run(["docker", "inspect", state["server_id"]], capture_output=True).returncode != 0
    traces = list((tmp_path / "verification").rglob("trace-*.jsonl"))
    assert len(traces) == count * 2
    if fault != "fixed_port":
        assert any('"data_hex"' in path.read_text() for path in traces)
    assert tree_hashes(baseline) == before


@pytest.mark.sandbox_integration
def test_real_server_and_checker_filesystems_cannot_cross(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    private = tmp_path / "private"; private.mkdir()
    (private / "secret").write_text("PRIVATE_CANARY")
    (project / "host-private").symlink_to(private)
    (project / "secret-source").write_text("GENERATED_CODE_CANARY")
    (project / "public.c").write_text("/* public source */\nint main(void) { return 0; }\n")
    # Fail if the server can read private files or write the mounted project.
    (project / "server.py").write_text('''import os, signal, socket, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
for path in ["/checks", "/verification", "/trusted", "/private", "/workspace/host-private", "/var/run/docker.sock"]:
    if Path(path).exists(): sys.exit(90)
try:
    Path("/workspace/received-secret").write_text("bad")
except OSError: pass
else: sys.exit(91)
with socket.socket() as s:
    s.bind((sys.argv[1], int(sys.argv[2]))); s.listen()
    while True:
        with s.accept()[0] as c:
            data=c.recv(1024)
            print(data.decode(), flush=True)
            print(data.hex(), flush=True)
            print("/workspace/"+data.hex()+".c:1", flush=True)
            print("/workspace/public.c:2", flush=True)
            print("/workspace/public.c:999999", flush=True)
            import base64
            print(base64.b64encode(data).decode(), flush=True)
            c.sendall(b"ok")
''')
    (private / "check.py").write_text('''import json, os, socket, sys, time
from pathlib import Path
assert not Path("/workspace/secret-source").exists()
assert not Path("/proc/1/root/workspace/secret-source").exists()
for _ in range(100):
    try: s=socket.create_connection((sys.argv[1],int(sys.argv[2]))); break
    except ConnectionRefusedError: time.sleep(.02)
with s:
    s.sendall(Path("/checks/secret").read_bytes())
    assert s.recv(2)==b"ok"
Path(os.environ["NEPA_ORACLE_TRACE_FILE"]).write_text("PRIVATE_CANARY")
print(json.dumps({"passed":True,"category":"isolation","observation":{"outcome":"accepted"}}))
''')
    target = {"builds": [{"id": "release", "artifact": "server.py"}], "run": ["python", "{artifact}", "{host}", "{port}"]}
    acceptance = {"checks": [{"id": "isolation", "required": True, "req_ids": [], "timeout_s": 5,
                              "argv": ["python", "/checks/check.py", "{host}", "{port}"]}]}
    result = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1)).run(
        target, acceptance, project, private, tmp_path / "verification", seed=1)
    assert result["passed"], result
    assert "PRIVATE_CANARY" in result["variants"][0]["detail"]["server_stdout"]
    view = json.dumps(safe_feedback(result))
    for token in ("PRIVATE_CANARY", "505249564154455f43414e415259", "UFJJVkFURV9DQU5BUlk="):
        assert token not in view
    assert not (project / "received-secret").exists()
    assert safe_feedback(result)["variants"][0]["detail"]["source_locations"] == [{"path": "public.c", "line": 2}]


@pytest.mark.sandbox_integration
@pytest.mark.parametrize("output", [
    '{"passed":false,"category":"wrong_response","observation":{"actual_type":32,"expected_type":144}}',
    '{}', 'not-json', '{"passed":true}',
])
def test_zero_oracle_exit_never_substitutes_for_a_complete_true_result(tmp_path, output):
    project = tmp_path / "project"; project.mkdir()
    private = tmp_path / "private"; private.mkdir()
    (project / "server.py").write_text("import signal,time,sys\nsignal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\nwhile True: time.sleep(.1)\n")
    (private / "oracle.py").write_text("print(" + repr(output) + ")\n")
    acceptance = {"checks": [{"id": "case", "required": True, "req_ids": [], "timeout_s": 2,
                              "argv": ["python", "/checks/oracle.py"]}]}
    target = {"builds": [{"id": "release", "artifact": "server.py"}], "run": ["python", "{artifact}"]}
    result = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1)).run(
        target, acceptance, project, private, tmp_path / "verification")
    assert result["passed"] is False
    assert result["variants"][0]["detail"]["checks"][0]["returncode"] == 0


@pytest.mark.sandbox_integration
def test_instrumented_asan_failure_overrides_successful_checker(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    private = tmp_path / "private"; private.mkdir()
    (project / "fault.c").write_text('''#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
static void stop(int sig) { (void)sig; _exit(0); }
int main(int argc, char **argv) {
    (void)argv;
    signal(SIGTERM, stop);
    int *p = malloc(sizeof(int));
    p[argc] = 7;
    free(p);
    for (;;) pause();
}
''')
    (private / "oracle.py").write_text('import json\nprint(json.dumps({"passed":True,"category":"fixture","observation":{"outcome":"accepted"}}))\n')
    executor = SandboxExecutor("nepa-sandbox:refactor", 1, 1)
    # Delay the fault until the checker starts so its genuine successful exit is observable.
    source = (project / "fault.c").read_text().replace("int *p =", "sleep(2); int *p =")
    (project / "fault.c").write_text(source)
    built = executor.exec(["gcc", "-g", "-fsanitize=address,undefined", "-fno-pie", "-no-pie", "fault.c", "-o", "fault"], str(project), 30)
    assert built.returncode == 0
    (private / "oracle.py").write_text('import json,time\ntime.sleep(3)\nprint(json.dumps({"passed":True,"category":"fixture","observation":{"outcome":"accepted"}}))\n')
    acceptance = {"checks": [{"id": "case", "required": True, "req_ids": [], "timeout_s": 5,
                              "argv": ["python", "/checks/oracle.py"]}]}
    target = {"builds": [{"id": "san", "artifact": "fault"}], "run": ["{artifact}"]}
    result = VerificationRunner(executor).run(target, acceptance, project, private, tmp_path / "verification")
    assert result["passed"] is False, result
    detail = result["variants"][0]["detail"]
    assert result["variants"][0]["execution"]["returncode"] == 0
    assert detail["sanitizer_error"] is True
    assert "address" in safe_feedback(result)["variants"][0]["detail"]["sanitizer_categories"]
    assert "heap-buffer-overflow" in detail["server_stderr"]
    assert safe_feedback(result)["variants"][0]["detail"]["source_locations"]


@pytest.mark.sandbox_integration
def test_recovery_cleans_only_recorded_run_containers(tmp_path):
    from nepa.run_store import atomic_json
    source = ROOT / "gold_file/mqtt"
    store = RunStore.initialize(tmp_path / "runs", source / "specIR.json", source / "target.json", source / "acceptance.json", load_config())
    executor = SandboxExecutor("nepa-sandbox:refactor", 1, 1)
    import uuid
    names = ["nepa-test-" + uuid.uuid4().hex for _ in range(2)]
    try:
        for name in names:
            executor.create_container(name, ["sleep", "60"], network="none", readonly={})
            subprocess.run(["docker", "start", name], check=True, capture_output=True)
        journal = store.root / "evidence/verification/containers.json"
        atomic_json(journal, {"server": names[0], "cleaned": False})
        store.recover()
        assert subprocess.run(["docker", "inspect", names[0]], capture_output=True).returncode != 0
        assert subprocess.run(["docker", "inspect", names[1]], capture_output=True).returncode == 0
        assert json.loads(journal.read_bytes())["cleaned"]
    finally:
        for name in names:
            executor.remove_container(name)


@pytest.mark.sandbox_integration
@pytest.mark.parametrize("noisy", ["server", "checker"])
def test_container_output_is_bounded_and_truncation_cannot_pass(tmp_path, noisy):
    project = tmp_path / "project"; project.mkdir()
    private = tmp_path / "private"; private.mkdir()
    (project / "server.py").write_text(
        'import signal,time,sys\nsignal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\n' +
        ('print("x"*100000,flush=True)\n' if noisy == "server" else '') +
        'while True: time.sleep(.1)\n')
    (private / "check.py").write_text('import json\nprint(json.dumps({"passed":True,"category":"fixture","observation":{"outcome":"accepted"}' +
        (',"extra":"' + 'x'*5000 + '"' if noisy == "checker" else '') + '}))\n')
    acceptance = {"checks": [{"id": "case", "required": True, "req_ids": [], "timeout_s": 3,
                              "argv": ["python", "/checks/check.py"]}]}
    target = {"builds": [{"id": "release", "artifact": "server.py"}], "run": ["python", "{artifact}"]}
    result = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1, max_output_bytes=1024)).run(
        target, acceptance, project, private, tmp_path / "verification")
    assert not result["passed"]
    assert result["variants"][0]["detail"]["output_truncated"]
    for path in (tmp_path / "verification/release").glob("*.*"):
        if path.suffix in {".stdout", ".stderr"}:
            assert path.stat().st_size <= 1024
    assert safe_feedback(result)["variants"][0]["detail"]["output_truncated"]


@pytest.mark.sandbox_integration
def test_killed_verifier_is_cleaned_by_run_recovery(tmp_path):
    import sys
    import time
    source = ROOT / "gold_file/mqtt"
    store = RunStore.initialize(tmp_path / "runs", source / "specIR.json", source / "target.json", source / "acceptance.json", load_config())
    (store.project / "server.py").write_text('import signal,time,sys\nsignal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\nwhile True: time.sleep(.1)\n')
    (store.private_checks / "waiting.py").write_text('import time\ntime.sleep(60)\n')
    store.run["current_task"] = "bootstrap"
    store.run["working_hashes"] = tree_hashes(store.project)
    store.save()
    target = {"builds": [{"id": "release", "artifact": "server.py"}], "run": ["python", "{artifact}"]}
    acceptance = {"checks": [{"id": "case", "required": True, "req_ids": [], "timeout_s": 60,
                              "argv": ["python", "/checks/waiting.py"]}]}
    evidence = store.root / "evidence/verification-killed"
    script = ('from pathlib import Path\nfrom nepa.tools.verification import VerificationRunner\n'
              'from nepa.tools.sandbox import SandboxExecutor\n'
              f'VerificationRunner(SandboxExecutor("nepa-sandbox:refactor",1,1)).run({target!r},{acceptance!r},'
              f'Path({str(store.project)!r}),Path({str(store.private_checks)!r}),Path({str(evidence)!r}))')
    process = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    journal = evidence / "release/containers.json"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if journal.exists() and json.loads(journal.read_bytes()).get("checker_id"):
                break
            assert process.poll() is None
            time.sleep(.05)
        ownership = json.loads(journal.read_bytes())
        assert ownership.get("checker_id") and not ownership["cleaned"]
        process.kill()
        process.wait()
        RunStore(store.root).recover()
        assert json.loads(journal.read_bytes())["cleaned"]
        for role in ("server_id", "checker_id"):
            assert subprocess.run(["docker", "inspect", ownership[role]], capture_output=True).returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        store.cleanup_verification()


@pytest.mark.sandbox_integration
def test_clean_early_server_exit_zero_does_not_pass_a_successful_oracle(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    private = tmp_path / "private"; private.mkdir()
    (project / "server.py").write_text('import time\ntime.sleep(2)\n')
    (private / "oracle.py").write_text('import time,json\ntime.sleep(3)\nprint(json.dumps({"passed":True,"category":"fixture","observation":{"outcome":"accepted"}}))\n')
    target = {"builds": [{"id": "release", "artifact": "server.py"}], "run": ["python", "{artifact}"]}
    acceptance = {"checks": [{"id": "case", "required": True, "req_ids": [], "timeout_s": 5,
                              "argv": ["python", "/checks/oracle.py"]}]}
    result = VerificationRunner(SandboxExecutor("nepa-sandbox:refactor", 1, 1)).run(
        target, acceptance, project, private, tmp_path / "verification")
    assert result["passed"] is False
    variant = result["variants"][0]
    assert variant["execution"]["returncode"] == 0
    assert variant["detail"]["early_exit"] == 0
    assert variant["detail"]["server_returncode"] == 0
    assert variant["detail"]["checks"][0]["passed"] is True
