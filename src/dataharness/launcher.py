"""本机前台运行时 Supervisor。

该模块把 OpenSandbox、FastAPI 和 Worker 的生命周期收口到一个可见的 Python 进程：
调用方只需要执行 ``python -m dataharness run``，所有子进程日志会带角色前缀写回当前
终端，按 ``Ctrl+C`` 时先请求 Worker drain，再停止 API 和 OpenSandbox。子进程仍然保持
独立宿主，避免把 API 崩溃、Worker 长任务和 Sandbox 进程混进同一个解释器。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .config import Settings, load_settings

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HTTP_ACCESS_LOG_RE = re.compile(
    r'"(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+[^" ]+\s+HTTP/\d(?:\.\d)?"'
)


def _marker_int(value: object, default: int) -> int:
    """把 JSON marker 的宽泛值安全转换为端口整数。"""

    if isinstance(value, (int, float, str)):
        return int(value)
    return default


class LaunchError(RuntimeError):
    """前台启动失败；消息只包含可操作的本机诊断，不包含密钥正文。"""


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """Supervisor 的小型 Interface。

    ``config_path`` 默认来自 ``.dataharness/setup.json`` 生成的受管配置；显式传入时
    仍要求配置包含固定 Sandbox digest。端口为 0 表示沿用 setup marker 的端口。
    """

    root: Path
    config_path: Path
    sandbox_config_path: Path
    sandbox_server_version: str
    api_port: int
    sandbox_port: int
    allow_missing_model_key: bool = False

    @classmethod
    def from_workspace(
        cls,
        root: Path,
        *,
        config_path: Path | None = None,
        sandbox_config_path: Path | None = None,
        sandbox_server_version: str | None = None,
        api_port: int = 0,
        sandbox_port: int = 0,
        allow_missing_model_key: bool = False,
    ) -> LaunchOptions:
        """从 setup marker 和命令行覆盖项组装启动参数。

        这样日常启动不必再记住 ``.dataharness/config.toml``、18080 和 OpenSandbox
        版本；marker 缺失时给出明确的“先 setup”提示，而不是让子进程报模糊错误。
        """

        resolved_root = root.resolve()
        marker_path = resolved_root / ".dataharness" / "setup.json"
        marker: dict[str, object] = {}
        if marker_path.is_file():
            try:
                loaded = json.loads(marker_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    marker = loaded
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise LaunchError(f"无法读取 {marker_path}；请重新运行 setup。") from error

        marker_config = marker.get("config_path")
        selected_config = config_path or (
            Path(str(marker_config)) if isinstance(marker_config, str) else None
        )
        if selected_config is None:
            candidate = resolved_root / "dataharness.local.toml"
            selected_config = (
                candidate
                if candidate.is_file()
                else resolved_root / ".dataharness" / "config.toml"
            )
        if not selected_config.is_absolute():
            selected_config = resolved_root / selected_config

        marker_sandbox = marker.get("sandbox_config_path")
        selected_sandbox = sandbox_config_path or (
            Path(str(marker_sandbox))
            if isinstance(marker_sandbox, str)
            else Path.home() / ".sandbox.toml"
        )
        if not selected_sandbox.is_absolute():
            selected_sandbox = resolved_root / selected_sandbox

        marker_version = marker.get("sandbox_server_version")
        selected_version = sandbox_server_version or str(marker_version or "0.2.2")
        selected_api_port = api_port or _marker_int(marker.get("api_port"), 8000)
        selected_sandbox_port = sandbox_port or _marker_int(marker.get("sandbox_port"), 18080)
        return cls(
            root=resolved_root,
            config_path=selected_config.resolve(),
            sandbox_config_path=selected_sandbox.resolve(),
            sandbox_server_version=selected_version,
            api_port=selected_api_port,
            sandbox_port=selected_sandbox_port,
            allow_missing_model_key=allow_missing_model_key,
        )


@dataclass(slots=True)
class _ChildProcess:
    """一个由 Supervisor 创建、输出和关闭的宿主进程。"""

    role: str
    process: subprocess.Popen[str]
    command: tuple[str, ...]
    start_time_utc: str
    log_path: Path
    output_thread: threading.Thread


@dataclass(slots=True)
class LocalRuntime:
    """运行本地应用的深模块。

    对外只暴露 ``run_forever``；启动顺序、健康检查、日志转发和 drain/关闭都隐藏在
    Implementation 内。这样命令行、未来桌面入口和测试可以共享同一套生命周期语义。
    """

    options: LaunchOptions
    settings: Settings = field(init=False)
    children: dict[str, _ChildProcess] = field(default_factory=dict, init=False)
    _redactions: tuple[str, ...] = field(default=(), init=False)
    _stopping: bool = field(default=False, init=False)
    _stop_requested: bool = field(default=False, init=False)
    _print_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        try:
            self.settings = load_settings(self.options.config_path)
        except Exception as error:  # noqa: BLE001 — CLI 边界统一转成可操作诊断
            raise LaunchError(f"配置校验失败：{type(error).__name__}") from error
        if not self.options.sandbox_config_path.is_file():
            raise LaunchError(f"OpenSandbox 配置不存在：{self.options.sandbox_config_path}")
        if not self.settings.sandbox.image_digest:
            raise LaunchError("配置缺少固定 Sandbox image_digest；请先运行 setup。")
        if not self.settings.model.api_key and not self.options.allow_missing_model_key:
            raise LaunchError(
                "模型 API Key 未配置；请在 dataharness.local.toml 的 [model].api_key 填写，"
                "或显式使用 --allow-missing-model-key。"
            )
        self._redactions = tuple(
            secret
            for secret in (self.settings.model.api_key, self.settings.sandbox.api_key)
            if secret
        )

    @property
    def _state_root(self) -> Path:
        """返回可重建的部署状态目录；不会触碰 Runtime 数据。"""

        return self.options.root / ".dataharness"

    @property
    def _logs_root(self) -> Path:
        """返回前台日志目录；终端仍是主要日志出口。"""

        return self._state_root / "logs"

    @property
    def _worker_health_path(self) -> Path:
        return self._state_root / "worker-health.json"

    @property
    def _worker_shutdown_path(self) -> Path:
        return self._state_root / "worker.shutdown"

    def run_forever(self) -> int:
        """启动完整本机应用并保持当前终端前台运行。

        返回值遵循 CLI 约定：正常 ``Ctrl+C`` 返回 0，启动失败或子进程异常退出返回 1。
        """

        previous_sigint = signal.getsignal(signal.SIGINT)

        def request_stop(_signum: int, _frame: object) -> None:
            """把 Ctrl+C 转成主循环状态，避免解释器在关闭中途抛出半截异常。"""

            self._stop_requested = True

        signal.signal(signal.SIGINT, request_stop)
        try:
            self.start()
            while not self._stopping and not self._stop_requested:
                for child in tuple(self.children.values()):
                    exit_code = child.process.poll()
                    if exit_code is not None:
                        self._emit(
                            f"[{child.role}] 进程已退出，退出码={exit_code}；正在关闭其他服务。"
                        )
                        self.stop()
                        return 1
                time.sleep(0.25)
            if self._stop_requested:
                self._emit("[runtime] 收到 Ctrl+C，开始优雅关闭（Worker 先 drain）。")
                self.stop()
                return 0
            return 0
        except KeyboardInterrupt:
            self._emit("[runtime] 收到 Ctrl+C，开始优雅关闭（Worker 先 drain）。")
            self.stop()
            return 0
        except LaunchError as error:
            self._emit(f"[runtime] 启动失败：{error}")
            self.stop()
            return 1
        except Exception as error:  # noqa: BLE001 — 前台入口不能留下孤儿进程
            self._emit(f"[runtime] 未处理异常：{type(error).__name__}: {error}")
            self.stop()
            return 1
        finally:
            signal.signal(signal.SIGINT, previous_sigint)

    def start(self) -> None:
        """按 Sandbox → API → Worker 顺序启动并验证三个宿主进程。"""

        if self.children:
            raise LaunchError("运行时已经启动；同一 Python 进程不能重复 start。")
        self._logs_root.mkdir(parents=True, exist_ok=True)
        self._worker_shutdown_path.unlink(missing_ok=True)
        self._worker_health_path.unlink(missing_ok=True)
        self._assert_port_available(self.options.sandbox_port, "OpenSandbox")
        self._assert_port_available(self.options.api_port, "API")

        uvx = shutil.which("uvx")
        if not uvx:
            raise LaunchError("未找到 uvx；请先安装 uv，不能启动 OpenSandbox。")
        python = self._python_executable()
        self._emit("[runtime] 启动 OpenSandbox、API、Worker；日志会实时显示在当前窗口。")

        sandbox_env = self._clean_child_environment()
        sandbox_env["OPENSANDBOX_INSECURE_SERVER"] = "YES"
        sandbox_env["NO_COLOR"] = "1"
        self._spawn(
            "sandbox",
            [
                uvx,
                "--from",
                f"opensandbox-server=={self.options.sandbox_server_version}",
                "opensandbox-server",
                "--config",
                str(self.options.sandbox_config_path),
            ],
            env=sandbox_env,
        )
        self._wait_for(
            "OpenSandbox",
            lambda: self._tcp_ready(self.options.sandbox_port),
            45,
            "sandbox",
        )

        api_env = os.environ.copy()
        api_env["PYTHONUNBUFFERED"] = "1"
        api_env["NO_COLOR"] = "1"
        api_env["DATAHARNESS_WORKER_HEALTH_FILE"] = str(self._worker_health_path)
        self._spawn(
            "api",
            [
                python,
                "-m",
                "dataharness",
                "serve",
                "--config",
                str(self.options.config_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.options.api_port),
            ],
            env=api_env,
        )
        self._wait_for(
            "API",
            lambda: self._http_ready(self.options.api_port),
            30,
            "api",
        )

        worker_env = os.environ.copy()
        worker_env["PYTHONUNBUFFERED"] = "1"
        worker_env["NO_COLOR"] = "1"
        self._spawn(
            "worker",
            [
                python,
                "-m",
                "dataharness",
                "worker",
                "--config",
                str(self.options.config_path),
                "--owner",
                "dataharness-foreground-worker",
                "--health-file",
                str(self._worker_health_path),
                "--shutdown-file",
                str(self._worker_shutdown_path),
                "--heartbeat-seconds",
                "1",
            ],
            env=worker_env,
        )
        self._wait_for("Worker", self._worker_ready, 20, "worker")
        self._emit(
            f"[runtime] READY；浏览器访问 http://127.0.0.1:{self.options.api_port}，"
            "按 Ctrl+C 关闭全部服务。"
        )
        self._write_manifest()

    def stop(self) -> None:
        """先 drain Worker，再停止 API/Sandbox；重复调用安全。"""

        if self._stopping:
            return
        self._stopping = True
        worker = self.children.get("worker")
        if worker and worker.process.poll() is None:
            self._worker_shutdown_path.parent.mkdir(parents=True, exist_ok=True)
            self._worker_shutdown_path.touch()
            self._wait_process(worker, 30)
        for role in ("api", "sandbox"):
            child = self.children.get(role)
            if child:
                self._terminate(child)
        for child in tuple(self.children.values()):
            child.output_thread.join(timeout=1)
        if self.children:
            self._write_manifest(stopped_at_utc=datetime.now(UTC).isoformat())
        self._worker_health_path.unlink(missing_ok=True)
        self._worker_shutdown_path.unlink(missing_ok=True)
        self.children.clear()
        self._emit("[runtime] 已关闭。")

    def _python_executable(self) -> str:
        """优先使用 setup 创建的 .venv，开发期 fallback 到当前 Python。"""

        candidates = (
            self.options.root / ".venv" / "Scripts" / "python.exe",
            self.options.root / ".venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return sys.executable

    def _clean_child_environment(self) -> dict[str, str]:
        """为 Sandbox 删除可能携带凭据的环境变量；模型 Key 不依赖环境变量。"""

        pattern = re.compile(r"(?i)(api.?key|secret|token|password|credential|private.?key)")
        return {name: value for name, value in os.environ.items() if not pattern.search(name)}

    def _spawn(self, role: str, command: list[str], *, env: dict[str, str]) -> None:
        """创建子进程并启动一条带角色前缀的输出转发线程。"""

        try:
            kwargs: dict[str, object] = {
                "cwd": str(self.options.root),
                "env": env,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
        except OSError as error:
            raise LaunchError(f"无法启动 {role}：{error}") from error
        log_path = self._logs_root / f"{role}.log"
        child = _ChildProcess(
            role=role,
            process=process,
            command=tuple(command),
            start_time_utc=datetime.now(UTC).isoformat(),
            log_path=log_path,
            output_thread=threading.Thread(),
        )
        child.output_thread = threading.Thread(
            target=self._relay_output,
            args=(child,),
            name=f"dataharness-log-{role}",
            daemon=True,
        )
        self.children[role] = child
        child.output_thread.start()

    def _relay_output(self, child: _ChildProcess) -> None:
        """同时把子进程日志写文件和当前终端，统一替换配置中的敏感值。"""

        stream = child.process.stdout
        if stream is None:
            return
        child.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with child.log_path.open("a", encoding="utf-8") as log:
                for raw_line in stream:
                    line = raw_line.rstrip("\r\n")
                    line = _ANSI_ESCAPE_RE.sub("", line)
                    # 访问日志只反映浏览器轮询、静态资源和健康探测；执行诊断关注
                    # Agent/Worker/Sandbox 事件，因此不写入受管日志文件或前台终端。
                    if "uvicorn.access" in line or _HTTP_ACCESS_LOG_RE.search(line):
                        continue
                    for secret in self._redactions:
                        line = line.replace(secret, "<redacted>")
                    log.write(line + "\n")
                    log.flush()
                    self._emit(f"[{child.role}] {line}")
        except (OSError, ValueError):
            # 日志是诊断副产物；即使日志目录不可写，也不能让 Supervisor 丢失关闭机会。
            return

    def _emit(self, message: str) -> None:
        with self._print_lock:
            print(message, flush=True)

    def _assert_port_available(self, port: int, role: str) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise LaunchError(f"{role} 端口 {port} 已被占用；请先停止旧服务。") from error

    def _wait_for(
        self,
        label: str,
        check: Callable[[], bool],
        timeout: float,
        role: str,
    ) -> None:
        """在有界时间内等待健康条件，并把提前退出转成具体角色诊断。"""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            child = self.children.get(role)
            if child and child.process.poll() is not None:
                raise LaunchError(
                    f"{label} 进程提前退出，退出码={child.process.returncode}。请查看当前日志。"
                )
            if check():
                return
            time.sleep(0.25)
        raise LaunchError(
            f"{label} 未在 {timeout:g}s 内就绪；请查看 .dataharness/logs/{role}.log。"
        )

    def _tcp_ready(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def _http_ready(self, port: int) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/readyz", timeout=0.8) as response:  # noqa: S310
                return 200 <= response.status < 300
        except (OSError, URLError):
            return False

    def _worker_ready(self) -> bool:
        try:
            payload = json.loads(self._worker_health_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("status") in {"IDLE", "RUNNING"}

    def _wait_process(self, child: _ChildProcess, timeout: float) -> None:
        try:
            child.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._emit(f"[{child.role}] drain 超时，终止受管进程树。")
            self._terminate(child)

    def _terminate(self, child: _ChildProcess) -> None:
        """只终止当前 Supervisor 创建的 PID；Windows 下递归收拢 uvx 子进程。"""

        if child.process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(child.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            child.process.terminate()
            try:
                child.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.process.kill()

    def _write_manifest(self, *, stopped_at_utc: str | None = None) -> None:
        """写入与旧 status/stop 兼容的派生 manifest，不保存模型密钥。"""

        roles: dict[str, dict[str, object]] = {}
        for child in self.children.values():
            command_match = {
                "sandbox": "opensandbox-server|uvx",
                "api": "dataharness.*serve",
                "worker": "dataharness.*worker",
            }[child.role]
            roles[child.role] = {
                "role": child.role,
                "pid": child.process.pid,
                "start_time_utc": child.start_time_utc,
                "log": str(child.log_path),
                "executable": child.command[0],
                "arguments": list(child.command[1:]),
                "command_match": command_match,
            }
        payload: dict[str, object] = {
            "schema_version": 1,
            "started_at_utc": datetime.now(UTC).isoformat(),
            "config_path": str(self.options.config_path),
            "api_port": self.options.api_port,
            "sandbox_port": self.options.sandbox_port,
            "roles": roles,
            "node_required_at_runtime": False,
            "data_deletion_performed": False,
        }
        if stopped_at_utc:
            payload["stopped_at_utc"] = stopped_at_utc
        state_path = self._state_root / "state.json"
        temporary = state_path.with_suffix(".json.tmp")
        try:
            self._state_root.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(state_path)
        except OSError:
            # status 是派生诊断；状态文件不可写时，前台运行本身仍可继续。
            temporary.unlink(missing_ok=True)
