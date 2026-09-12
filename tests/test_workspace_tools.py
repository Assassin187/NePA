import pytest
from nepa.tools.workspace import WorkspaceTools
from nepa.tools.sandbox import SandboxExecutor

@pytest.fixture
def tools(tmp_path):
    for name in ("project", "inputs", "evidence"):
        (tmp_path / name).mkdir()
    return WorkspaceTools(tmp_path / "project", tmp_path / "inputs", tmp_path / "evidence",
                          SandboxExecutor("nepa-sandbox:refactor", 1, 1))

def test_write_read_replace_and_search(tools):
    tools.execute("write_file", {"path": "src/a.c", "content": "int a;\n"})
    assert tools.execute("read_file", {"path": "src/a.c"})["content"] == "int a;\n"
    tools.execute("replace_text", {"path": "src/a.c", "old": "a;", "new": "b;"})
    assert tools.execute("search", {"pattern": "int b"})["matches"][0]["line"] == 1
    assert tools.execute("search", {"pattern": "absent|int b"})["matches"][0]["line"] == 1
    with pytest.raises(ValueError, match="regular expression"):
        tools.execute("search", {"pattern": "["})
    with pytest.raises(ValueError):
        tools.execute("replace_text", {"path": "src/a.c", "old": "absent", "new": "x"})

@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", ".git/config", "inputs/spec.json", "evidence/a.json"])
def test_protected_writes(tools, path):
    with pytest.raises(ValueError):
        tools.execute("write_file", {"path": path, "content": "no"})

def test_symlink_escape(tools, tmp_path):
    (tools.project / "escape").symlink_to(tmp_path / "inputs", target_is_directory=True)
    with pytest.raises(ValueError):
        tools.execute("write_file", {"path": "escape/x", "content": "no"})

def test_input_pointer_and_pagination(tools):
    (tools.inputs / "spec.json").write_text('{"values":[{"x":"abcdefghij"}]}')
    result = tools.execute("read_file", {"path": "inputs/spec.json", "json_pointer": "/values/0/x", "limit": 5})
    assert result["content"] == '"abcd'
    assert result["next_offset"] == 5

@pytest.mark.sandbox_integration
def test_oracle_is_readable_but_not_writable(tools):
    checks = tools.inputs / "checks"
    checks.mkdir()
    (checks / "check.py").write_text("print('trusted')\n")
    assert tools.execute("list_files", {"path": "inputs"})["files"][0]["path"] == "inputs/checks/check.py"
    assert tools.execute("search", {"path": "inputs", "pattern": "trusted"})["matches"]
    with pytest.raises(ValueError):
        tools.execute("write_file", {"path": "inputs/checks/check.py", "content": "changed"})
    result = tools.execute("run_command", {"argv": ["sh", "-c", "python /checks/check.py && ! touch /checks/changed"]})
    assert result["returncode"] == 0 and "trusted" in result["stdout"]
    assert not (checks / "changed").exists()

@pytest.mark.sandbox_integration
def test_real_compile_failure_then_fix(tools):
    tools.execute("write_file", {"path": "a.c", "content": "int main(void){ broken syntax }"})
    bad = tools.execute("run_command", {"argv": ["gcc", "-std=c99", "-Wall", "-Werror", "a.c", "-o", "a"]})
    assert bad["returncode"] != 0
    assert "error:" in bad["stderr"]
    tools.execute("write_file", {"path": "a.c", "content": "int main(void){return 0;}"})
    good = tools.execute("run_command", {"argv": ["gcc", "-std=c99", "-Wall", "-Werror", "a.c", "-o", "a"]})
    assert good["returncode"] == 0
    assert tools.execute("run_command", {"argv": ["./a"]})["returncode"] == 0

@pytest.mark.sandbox_integration
def test_sandbox_has_no_protocol_server_or_secret(tools, monkeypatch):
    monkeypatch.setenv("NEPA_DS_API_KEY", "not-mounted")
    result = tools.execute("run_command", {"argv": ["sh", "-c", 'test -z "$NEPA_DS_API_KEY" && ! command -v mosquitto']})
    assert result["returncode"] == 0
