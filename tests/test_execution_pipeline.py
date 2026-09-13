"""Actual CLI/engine/sandbox wiring with a test-only provider, not live evidence."""
import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
import nepa.cli as cli
from nepa.application import build_orchestrator
from nepa.llm.client import LLMResponse
from nepa.config import load_config
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider

ROOT = Path(__file__).parents[1]
SOURCE = r"""
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
static volatile sig_atomic_t stopping;
static void stop(int signo) { (void)signo; stopping = 1; }
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    struct sigaction sa = {0}; sa.sa_handler = stop;
    sigaction(SIGTERM, &sa, 0);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return 3;
    int yes = 1; setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons((unsigned short)atoi(argv[4]));
    if (inet_pton(AF_INET, argv[2], &address.sin_addr) != 1) return 4;
    if (bind(fd, (struct sockaddr *)&address, sizeof address) || listen(fd, 4)) return 5;
    while (!stopping) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, 100) <= 0) continue;
        int c = accept(fd, 0, 0);
        if (c >= 0) {
            unsigned char byte;
            if (read(c, &byte, 1) == 1) (void)write(c, &byte, 1);
            close(c);
        }
    }
    close(fd); return 0;
}
"""
MAKEFILE = ("release:\n\tmkdir -p build/release\n\tgcc -std=c99 -Wall -Wextra -Werror main.c -o build/release/protocol-server\n"
            "san:\n\tmkdir -p build/san\n\tgcc -std=c99 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-pie -no-pie main.c -o build/san/protocol-server\n"
            "clean:\n\trm -rf build\n")
CHECK = """
import json, socket, sys, time
for attempt in range(100):
    try:
        s = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1)
        break
    except OSError:
        time.sleep(.03)
else:
    raise AssertionError("not ready")
with s:
    s.sendall(bytes([1]))
    assert s.recv(1) == bytes([1])
print(json.dumps({"passed": True, "category": "echo", "observation": {"matched": True}}))
"""

class TestProvider:
    __test__ = False
    native_structured_output = False
    def __init__(self):
        self.bootstrap = 0
        self.calls = 0
        config = load_config()
        self.adapter = OpenAICompatibleProvider("deepseek", config.providers["deepseek"])
    def prepare(self, request, **kwargs):
        self.current_request = request
        return self.adapter.prepare(request, **kwargs)
    def send(self, prepared):
        self.calls += 1
        task = json.loads(self.current_request.user)["task"]
        if task["id"] == "bootstrap" and self.bootstrap < 3:
            name, content = [("main.c", SOURCE), ("Makefile", MAKEFILE), ("README.md", "Test-only echo project")][self.bootstrap]
            self.bootstrap += 1
            action = {"tool": "write_file", "arguments": {"path": name, "content": content}}
        else:
            claims = [{"id": ref, "status": "already_present", "reason": "test provider claim, not semantic proof",
                       "code_refs": ["main.c:1"]} for ref in task["requirement_ids"]]
            action = {"tool": "finish", "arguments": {"summary": "request host checks", "claims": claims}}
        return LLMResponse(text=json.dumps(action), tokens_in=10, tokens_out=10, cost_cny=0, model=prepared.model,
                           parameter_support={}, provider_metadata={"finish_reason": "stop",
                           "returned_model_identity_observed": True, "returned_model_identity": prepared.model,
                           "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

@pytest.mark.sandbox_integration
def test_real_cli_to_export_and_read_only_status(tmp_path, monkeypatch):
    (tmp_path / "check.py").write_text(CHECK)
    acceptance = {"schema_version": "1.0", "description": "test-only independent echo oracle",
                  "assets": ["check.py"], "checks": [{"id": "echo", "required": True,
                  "argv": ["python", "/checks/check.py", "{host}", "{port}"], "timeout_s": 10, "req_ids": []}]}
    (tmp_path / "acceptance.json").write_text(json.dumps(acceptance))
    provider = TestProvider()
    monkeypatch.setattr(cli, "build_orchestrator", lambda store: build_orchestrator(store, {"deepseek": provider}))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--spec", str(ROOT / "tests/fixtures/non_mqtt_application/spec.json"),
                          "--target", str(ROOT / "gold_file/mqtt/target.json"), "--acceptance", str(tmp_path / "acceptance.json"),
                          "--runs-root", str(tmp_path / "runs")])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    run_dir = Path(value["run_dir"])
    report = json.loads((run_dir / "report.json").read_bytes())
    assert report["status"] == "success"
    assert report["schema_version"] == "5.0" and report["exposure"] == "private_isolated"
    state = json.loads((run_dir / "run.json").read_bytes())
    assert state["schema_version"] == "7.0" and state["config_snapshot"]["schema_version"] == "3.0"
    assert "acceptance" not in state["inputs"]
    assert (run_dir / "private/assets/check.py").is_file()
    assert not (run_dir / "inputs/checks").exists()
    from nepa.speclib.lint import _schema_errors
    assert not _schema_errors(report, "report.schema.json")
    assert value["tasks_passed"] == value["tasks_total"] == 5
    assert provider.calls == 8
    assert (run_dir / "delivery/build/release/protocol-server").is_file()
    assert (run_dir / "delivery/build/san/protocol-server").is_file()
    before = (run_dir / "run.json").read_bytes()
    assert runner.invoke(cli.app, ["status", value["run_id"], "--runs-root", str(tmp_path / "runs")]).exit_code == 0
    assert (run_dir / "run.json").read_bytes() == before
    assert runner.invoke(cli.app, ["resume", value["run_id"], "--runs-root", str(tmp_path / "runs")]).exit_code == 0
    assert provider.calls == 8


def test_cli_rejects_legacy_config_before_initialization(tmp_path, capsys):
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"schema_version":"2.0"}')
    code = cli.main(["run", "--spec", str(ROOT / "gold_file/mqtt/specIR.json"),
                     "--target", str(ROOT / "gold_file/mqtt/target.json"),
                     "--acceptance", str(ROOT / "gold_file/mqtt/acceptance.json"),
                     "--config", str(legacy), "--runs-root", str(tmp_path / "runs")])
    assert code == 20
    assert "baseline" in json.loads(capsys.readouterr().out)["error"]
    assert not (tmp_path / "runs").exists()


def test_cli_rejects_legacy_run_without_migrating_it(tmp_path, capsys):
    from nepa.run_store import RunStore
    store = RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                                ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", load_config())
    legacy = {**store.run, "schema_version": "6.0"}
    path = store.root / "run.json"
    path.write_text(json.dumps(legacy))
    before = path.read_bytes()
    assert cli.main(["resume", store.run_id, "--runs-root", str(store.root.parent)]) == 20
    assert "baseline" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before
