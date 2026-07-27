"""nepa.tools.sandbox 单元测试（设计文档 8.5）。

纯单元部分全部 mock subprocess，不做真实 docker 调用；
真实容器执行的用例标 @pytest.mark.integration，docker/镜像不可用时 skip。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from typing import Any
from unittest.mock import patch

import pytest

from nepa.tools.sandbox import (
    DEFAULT_IMAGE,
    ExecResult,
    Sandbox,
    SandboxUnavailableError,
)


class FakeDocker:
    """subprocess.run 替身：按 docker 子命令分派，记录全部调用。"""

    def __init__(
        self,
        *,
        docker_ok: bool = True,
        docker_missing: bool = False,
        network_exists: bool = True,
        main_returncode: int = 0,
        main_stdout: str = "out",
        main_stderr: str = "err",
        main_raises: BaseException | None = None,
    ) -> None:
        self.docker_ok = docker_ok
        self.docker_missing = docker_missing
        self.network_exists = network_exists
        self.main_returncode = main_returncode
        self.main_stdout = main_stdout
        self.main_stderr = main_stderr
        self.main_raises = main_raises
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        sub = argv[1]
        if self.docker_missing:
            raise FileNotFoundError(argv[0])
        if sub == "info":
            if self.docker_ok:
                return CompletedProcess(argv, 0, "24.0.0\n", "")
            return CompletedProcess(argv, 1, "", "Cannot connect to the Docker daemon")
        if sub == "network" and argv[2] == "inspect":
            code = 0 if self.network_exists else 1
            return CompletedProcess(argv, code, "", "" if code == 0 else "not found")
        if sub == "network" and argv[2] == "create":
            return CompletedProcess(argv, 0, "net-id\n", "")
        if sub == "kill":
            return CompletedProcess(argv, 0, "", "")
        assert sub == "run", f"未预期的 docker 子命令: {argv}"
        if self.main_raises is not None:
            raise self.main_raises
        return CompletedProcess(argv, self.main_returncode, self.main_stdout, self.main_stderr)

    def subcommands(self) -> list[str]:
        return [argv[1] for argv, _ in self.calls]

    def run_call(self) -> tuple[list[str], dict[str, Any]]:
        for argv, kwargs in self.calls:
            if argv[1] == "run":
                return argv, kwargs
        raise AssertionError("没有发生 docker run 调用")


# ---------------------------------------------------------------------- #
# 命令行构造（纯函数，无 mock）
# ---------------------------------------------------------------------- #


class TestBuildCommand:
    def test_none_mode_full_argv(self, tmp_path: Path) -> None:
        """net=none：--network=none，参数顺序与 8.5 约定一致。"""
        sb = Sandbox()
        argv = sb.build_command(["make", "SAN=1"], str(tmp_path), "none", name="nepa-test")
        assert argv == [
            "docker",
            "run",
            "--rm",
            "--name=nepa-test",
            "--network=none",
            "--cpus=2",
            "--memory=4g",
            "-v",
            f"{tmp_path.resolve()}:/w",
            "-w",
            "/w",
            DEFAULT_IMAGE,
            "make",
            "SAN=1",
        ]

    def test_loopback_mode_also_uses_network_none(self, tmp_path: Path) -> None:
        """net=loopback：仍是 --network=none（容器内 lo 可用即满足 L2 回环，8.5/6.7）。"""
        argv = Sandbox().build_command(["./run"], str(tmp_path), "loopback")
        assert "--network=none" in argv
        assert not any(a.startswith("--network=nepa-") for a in argv)

    def test_internal_mode_uses_internal_network(self, tmp_path: Path) -> None:
        """net=internal：使用 docker 内部网络（8.5：L3 连 mosquitto 容器）。"""
        argv = Sandbox().build_command(["./run"], str(tmp_path), "internal")
        assert "--network=nepa-internal" in argv
        assert "--network=none" not in argv

    def test_default_net_is_none(self, tmp_path: Path) -> None:
        """默认网络模式为 none（8.5：默认 --network=none）。"""
        argv = Sandbox().build_command(["true"], str(tmp_path))
        assert "--network=none" in argv

    def test_resource_flags_follow_config(self, tmp_path: Path) -> None:
        """--cpus/--memory 来自配置（8.5/8.3 sandbox: cpu, mem_gb）。"""
        sb = Sandbox(image="img:1", cpu=1.5, mem_gb=2.0)
        argv = sb.build_command(["true"], str(tmp_path))
        assert "--cpus=1.5" in argv
        assert "--memory=2g" in argv
        assert "img:1" in argv

    def test_generated_container_name_prefix(self, tmp_path: Path) -> None:
        """未显式给 name 时自动生成 nepa-<uuid>。"""
        argv = Sandbox().build_command(["true"], str(tmp_path))
        names = [a for a in argv if a.startswith("--name=")]
        assert len(names) == 1
        assert names[0].startswith("--name=nepa-")
        assert len(names[0]) > len("--name=nepa-")

    def test_rejects_invalid_net(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="网络模式"):
            Sandbox().build_command(["true"], str(tmp_path), "host")  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# exec 行为（mock subprocess.run）
# ---------------------------------------------------------------------- #


class TestExec:
    def test_success_populates_result(self, tmp_path: Path) -> None:
        fake = FakeDocker(main_returncode=3, main_stdout="hello", main_stderr="warn")
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            result = Sandbox().exec(["make"], str(tmp_path), timeout_s=30)
        assert isinstance(result, ExecResult)
        assert result.code == 3
        assert result.stdout == "hello"
        assert result.stderr == "warn"
        assert result.timed_out is False
        assert result.duration_ms >= 0

    def test_timeout_s_passed_to_subprocess(self, tmp_path: Path) -> None:
        """业务超时 timeout_s 原样传给 docker run 的 subprocess 调用。"""
        fake = FakeDocker()
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            Sandbox().exec(["make"], str(tmp_path), timeout_s=42)
        _, kwargs = fake.run_call()
        assert kwargs["timeout"] == 42

    def test_timeout_kills_container_and_flags_result(self, tmp_path: Path) -> None:
        """超时：docker kill 强杀同名容器并标记 timed_out（8.5）。"""
        fake = FakeDocker(main_raises=TimeoutExpired(cmd=["docker", "run"], timeout=5))
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            result = Sandbox().exec(["sleep", "999"], str(tmp_path), timeout_s=5)
        assert result.timed_out is True
        assert result.code == -1  # 强杀后无有效退出码
        run_argv, _ = fake.run_call()
        name = next(a for a in run_argv if a.startswith("--name=")).removeprefix("--name=")
        kill_calls = [argv for argv, _ in fake.calls if argv[1] == "kill"]
        assert kill_calls == [["docker", "kill", name]]

    def test_timeout_preserves_partial_output(self, tmp_path: Path) -> None:
        """超时时保留 TimeoutExpired 携带的部分输出（bytes 也应转 str）。"""
        exc = TimeoutExpired(cmd=["docker", "run"], timeout=5, output=b"partial", stderr=None)
        fake = FakeDocker(main_raises=exc)
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            result = Sandbox().exec(["make"], str(tmp_path), timeout_s=5)
        assert result.stdout == "partial"
        assert result.stderr == ""

    def test_internal_creates_network_when_missing(self, tmp_path: Path) -> None:
        """internal 且网络不存在：先 network create --internal 再 run（8.5）。"""
        fake = FakeDocker(network_exists=False)
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            Sandbox().exec(["true"], str(tmp_path), timeout_s=5, net="internal")
        create_calls = [argv for argv, _ in fake.calls if argv[1:3] == ["network", "create"]]
        assert create_calls == [["docker", "network", "create", "--internal", "nepa-internal"]]
        subs = fake.subcommands()
        assert subs.index("network") < subs.index("run")

    def test_internal_skips_create_when_network_exists(self, tmp_path: Path) -> None:
        fake = FakeDocker(network_exists=True)
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            Sandbox().exec(["true"], str(tmp_path), timeout_s=5, net="internal")
        assert not any(argv[1:3] == ["network", "create"] for argv, _ in fake.calls)

    def test_rejects_missing_cwd_before_any_docker_call(self, tmp_path: Path) -> None:
        fake = FakeDocker()
        with (
            patch("nepa.tools.sandbox.subprocess.run", fake),
            pytest.raises(ValueError, match="workspace"),
        ):
            Sandbox().exec(["true"], str(tmp_path / "no-such-dir"), timeout_s=5)
        assert fake.calls == []

    def test_rejects_invalid_net_before_any_docker_call(self, tmp_path: Path) -> None:
        fake = FakeDocker()
        with (
            patch("nepa.tools.sandbox.subprocess.run", fake),
            pytest.raises(ValueError, match="网络模式"),
        ):
            Sandbox().exec(["true"], str(tmp_path), timeout_s=5, net="bridge")  # type: ignore[arg-type]
        assert fake.calls == []

    def test_on_event_hook_receives_exec_record(self, tmp_path: Path) -> None:
        """on_event 收到 exec 事件（供上层写 stage_events.ndjson，8.5）。"""
        events: list[dict[str, Any]] = []
        fake = FakeDocker()
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            Sandbox(on_event=events.append).exec(["make"], str(tmp_path), timeout_s=9)
        assert len(events) == 1
        event = events[0]
        assert event["tool"] == "sandbox.exec"
        assert event["cmd"] == ["make"]
        assert event["net"] == "none"
        assert event["code"] == 0
        assert event["timed_out"] is False


# ---------------------------------------------------------------------- #
# docker 不可用（8.5 MUST：禁止降级到宿主机）
# ---------------------------------------------------------------------- #


class TestUnavailable:
    def test_docker_binary_missing_raises_with_guidance(self, tmp_path: Path) -> None:
        fake = FakeDocker(docker_missing=True)
        with (
            patch("nepa.tools.sandbox.subprocess.run", fake),
            pytest.raises(SandboxUnavailableError) as excinfo,
        ):
            Sandbox().exec(["make"], str(tmp_path), timeout_s=5)
        msg = str(excinfo.value)
        assert "8.5" in msg  # 指明设计依据
        assert "docker build" in msg  # 指明镜像构建方式
        assert "禁止" in msg  # 指明不降级

    def test_daemon_down_raises(self, tmp_path: Path) -> None:
        fake = FakeDocker(docker_ok=False)
        with (
            patch("nepa.tools.sandbox.subprocess.run", fake),
            pytest.raises(SandboxUnavailableError, match="Cannot connect"),
        ):
            Sandbox().exec(["make"], str(tmp_path), timeout_s=5)

    def test_no_host_fallback_on_unavailable(self, tmp_path: Path) -> None:
        """docker 不可用时绝不执行业务命令（既不 docker run 也不宿主机直跑）。"""
        fake = FakeDocker(docker_ok=False)
        with (
            patch("nepa.tools.sandbox.subprocess.run", fake),
            pytest.raises(SandboxUnavailableError),
        ):
            Sandbox().exec(["rm", "-rf", "x"], str(tmp_path), timeout_s=5)
        assert fake.subcommands() == ["info"]  # 只发生了可用性探测

    def test_availability_probe_cached_after_success(self, tmp_path: Path) -> None:
        """探测通过后缓存，连续 exec 只探测一次。"""
        fake = FakeDocker()
        with patch("nepa.tools.sandbox.subprocess.run", fake):
            sb = Sandbox()
            sb.exec(["true"], str(tmp_path), timeout_s=5)
            sb.exec(["true"], str(tmp_path), timeout_s=5)
        assert fake.subcommands().count("info") == 1


# ---------------------------------------------------------------------- #
# 集成测试：需要真实 docker 与已构建的沙箱镜像
# ---------------------------------------------------------------------- #


def _integration_skip_reason() -> str | None:
    """docker 或沙箱镜像不可用时返回 skip 原因。"""
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "docker 不可用"
    if proc.returncode != 0:
        return "docker daemon 不可用或无权限"
    image = subprocess.run(
        ["docker", "image", "inspect", DEFAULT_IMAGE], capture_output=True, timeout=10, check=False
    )
    if image.returncode != 0:
        return f"沙箱镜像 {DEFAULT_IMAGE} 未构建（见 docker/sandbox.Dockerfile 头部说明）"
    return None


_SKIP_REASON = _integration_skip_reason()


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
class TestSandboxIntegration:
    def test_echo_and_workspace_mount(self, tmp_path: Path) -> None:
        tmp_path.chmod(0o755)
        fixture = tmp_path / "hello.txt"
        fixture.write_text("from-host\n")
        fixture.chmod(0o644)
        result = Sandbox().exec(["cat", "/w/hello.txt"], str(tmp_path), timeout_s=60)
        assert result.code == 0, result.stderr
        assert "from-host" in result.stdout
        assert result.timed_out is False

    def test_loopback_lo_usable_under_network_none(self, tmp_path: Path) -> None:
        """--network=none 下容器内 lo 可绑定，验证 L2 回环假设（8.5/6.7）。"""
        tmp_path.chmod(0o755)
        code = "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print('lo-ok')"
        result = Sandbox().exec(
            ["python3", "-c", code], str(tmp_path), timeout_s=60, net="loopback"
        )
        assert result.code == 0, result.stderr
        assert "lo-ok" in result.stdout

    def test_timeout_kills_real_container(self, tmp_path: Path) -> None:
        tmp_path.chmod(0o755)
        result = Sandbox().exec(["sleep", "300"], str(tmp_path), timeout_s=2)
        assert result.timed_out is True
        assert result.duration_ms < 60_000
