"""沙箱执行层：docker run 封装（设计文档 8.5）。

生成代码是不可信代码，一切构建/测试副作用只在容器内发生；
docker 不可用时抛 SandboxUnavailableError，**禁止**静默降级到宿主机执行（8.5 MUST）。

网络模式（8.5）：
- "none"：默认，--network=none，完全禁网；
- "loopback"：仍用 --network=none——该模式下容器内 lo 接口可用，
  满足 L2 回环 TCP 测试（6.7：起真实进程走回环 TCP）；
- "internal"：docker 内部网络（--internal，无外网出口），供 L3 连 mosquitto 容器。
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

NetMode = Literal["none", "loopback", "internal"]

_VALID_NET_MODES: frozenset[str] = frozenset(("none", "loopback", "internal"))

#: 默认镜像名，与 configs/default.yaml 的 sandbox.image 一致（8.3）
DEFAULT_IMAGE = "nepa-sandbox:latest"

#: docker 辅助命令（info / network / kill）的固定超时，与业务 timeout_s 无关
_DOCKER_ADMIN_TIMEOUT_S = 15

_UNAVAILABLE_HINT = """docker 不可用：{reason}
沙箱执行已拒绝——生成代码是不可信代码，禁止在宿主机直接执行（设计文档 8.5 MUST），
NePA 不会降级到宿主机运行。排查步骤：
  1. 安装 docker 并启动 daemon：https://docs.docker.com/engine/install/
  2. 确认当前用户可访问 /var/run/docker.sock（加入 docker 组后重新登录）：docker info
  3. 构建沙箱镜像（M0-8）：docker build -t {image} -f docker/sandbox.Dockerfile docker"""


class SandboxUnavailableError(RuntimeError):
    """docker 不可用（未安装 / daemon 未启动 / 无权限）。见 8.5。"""


@dataclass(frozen=True, slots=True)
class ExecResult:
    """一次沙箱执行的结果（8.5：{code, stdout, stderr, duration_ms, timed_out}）。

    超时时 code 固定为 -1（容器被强杀，无有效退出码），timed_out=True。
    """

    code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


def _fmt_num(value: float) -> str:
    """2.0 -> "2"、1.5 -> "1.5"，用于 --cpus/--memory 参数。"""
    return format(value, "g")


def _as_text(data: str | bytes | None) -> str:
    """TimeoutExpired 携带的部分输出可能为 bytes 或 None，统一转 str。"""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


class Sandbox:
    """docker run 执行封装（8.5）。

    构造参数镜像 configs/default.yaml 的 sandbox 段（8.3）：
    image / cpu / mem_gb；CPU 与内存限额随每次 exec 附加到 docker run。

    ``on_event`` 为可选回调：每次 exec 结束后收到一条事件 dict，
    供上层（telemetry/orchestrator）写入 stage_events.ndjson
    （8.5：每次 exec 记入 stage_events.ndjson；本模块不直接持有 run 目录）。
    """

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        cpu: float = 2.0,
        mem_gb: float = 4.0,
        *,
        docker_bin: str = "docker",
        internal_network: str = "nepa-internal",
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.image = image
        self.cpu = cpu
        self.mem_gb = mem_gb
        self.docker_bin = docker_bin
        self.internal_network = internal_network
        self._on_event = on_event
        self._docker_ok = False  # _ensure_docker 通过后缓存，避免每次 exec 重复探测

    # ------------------------------------------------------------------ #
    # 命令构造（纯函数，便于单测）
    # ------------------------------------------------------------------ #

    def build_command(
        self,
        cmd: Sequence[str],
        cwd: str,
        net: NetMode = "none",
        *,
        name: str | None = None,
    ) -> list[str]:
        """构造 docker run 命令行（8.5）。

        none 与 loopback 都映射为 --network=none：--network=none 下容器内
        lo 可用，已满足 L2 回环需求；internal 映射为 docker 内部网络。
        """
        if net not in _VALID_NET_MODES:
            raise ValueError(f"非法网络模式 {net!r}，可选: none | loopback | internal（8.5）")
        container_name = name or f"nepa-{uuid.uuid4().hex}"
        network = self.internal_network if net == "internal" else "none"
        host_dir = Path(cwd).resolve()
        return [
            self.docker_bin,
            "run",
            "--rm",
            f"--name={container_name}",
            f"--network={network}",
            f"--cpus={_fmt_num(self.cpu)}",  # CPU/内存限额来自配置（8.5/8.3）
            f"--memory={_fmt_num(self.mem_gb)}g",
            "-v",
            f"{host_dir}:/w",  # workspace 以卷挂载（8.5）
            "-w",
            "/w",
            self.image,
            *cmd,
        ]

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #

    def exec(
        self,
        cmd: list[str],
        cwd: str,
        timeout_s: int,
        net: NetMode = "none",
    ) -> ExecResult:
        """在沙箱容器中执行 cmd，cwd（宿主 workspace 路径）挂载为容器内 /w。

        超时后 docker kill 强杀容器并回收（8.5：超时强杀并回收子进程），
        结果标记 timed_out=True、code=-1。
        """
        if net not in _VALID_NET_MODES:
            raise ValueError(f"非法网络模式 {net!r}，可选: none | loopback | internal（8.5）")
        workdir = Path(cwd).resolve()
        if not workdir.is_dir():
            raise ValueError(f"workspace 不存在或不是目录: {workdir}")

        self._ensure_docker()
        if net == "internal":
            self._ensure_internal_network()

        container_name = f"nepa-{uuid.uuid4().hex}"
        argv = self.build_command(cmd, str(workdir), net, name=container_name)

        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s, check=False
            )
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            # subprocess 超时只杀了 docker 客户端进程，容器还在跑：按名强杀（8.5）
            timed_out = True
            code = -1
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr)
            self._kill(container_name)
        duration_ms = int((time.monotonic() - start) * 1000)

        result = ExecResult(
            code=code, stdout=stdout, stderr=stderr, duration_ms=duration_ms, timed_out=timed_out
        )
        if self._on_event is not None:
            self._on_event(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "tool": "sandbox.exec",
                    "image": self.image,
                    "cmd": list(cmd),
                    "cwd": str(workdir),
                    "net": net,
                    "code": result.code,
                    "duration_ms": result.duration_ms,
                    "timed_out": result.timed_out,
                }
            )
        return result

    # ------------------------------------------------------------------ #
    # docker 环境探测与辅助
    # ------------------------------------------------------------------ #

    def _ensure_docker(self) -> None:
        """探测 docker 可用性；失败抛 SandboxUnavailableError（禁止宿主机降级，8.5）。"""
        if self._docker_ok:
            return
        try:
            proc = subprocess.run(
                [self.docker_bin, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=_DOCKER_ADMIN_TIMEOUT_S,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailableError(
                _UNAVAILABLE_HINT.format(
                    reason=f"未找到可执行文件 {self.docker_bin!r}", image=self.image
                )
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxUnavailableError(
                _UNAVAILABLE_HINT.format(reason="docker info 探测超时", image=self.image)
            ) from exc
        if proc.returncode != 0:
            reason = proc.stderr.strip() or "docker daemon 无响应或无权限"
            raise SandboxUnavailableError(_UNAVAILABLE_HINT.format(reason=reason, image=self.image))
        self._docker_ok = True

    def _ensure_internal_network(self) -> None:
        """internal 模式：docker 内部网络不存在则创建（8.5：L3 用 docker 内部网络）。"""
        inspect = subprocess.run(
            [self.docker_bin, "network", "inspect", self.internal_network],
            capture_output=True,
            text=True,
            timeout=_DOCKER_ADMIN_TIMEOUT_S,
            check=False,
        )
        if inspect.returncode == 0:
            return
        create = subprocess.run(
            # --internal：无外网出口的 docker 网络，仅容器互联
            [self.docker_bin, "network", "create", "--internal", self.internal_network],
            capture_output=True,
            text=True,
            timeout=_DOCKER_ADMIN_TIMEOUT_S,
            check=False,
        )
        if create.returncode != 0:
            raise SandboxUnavailableError(
                f"无法创建 docker 内部网络 {self.internal_network!r}: "
                f"{create.stderr.strip() or '未知错误'}"
            )

    def _kill(self, container_name: str) -> None:
        """超时强杀容器；--rm 保证 kill 后由 daemon 回收，失败静默（尽力而为）。"""
        try:
            subprocess.run(
                [self.docker_bin, "kill", container_name],
                capture_output=True,
                timeout=_DOCKER_ADMIN_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
