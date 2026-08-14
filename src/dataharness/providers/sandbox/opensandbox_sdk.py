"""官方 ``opensandbox`` SDK 的受控实现：把 OpenSandboxClient 协议映射到真实服务。

本模块是部署装配层（deployment assembly）的一部分：它把 ``SandboxSpec`` 翻译成官方 SDK
的创建参数，把真实 Sandbox 的运行时事实翻译成 ``SandboxAttestation``，把独立 Step 进程
翻译成 ``ExecutionResult``。上层（analysis、agent）从不导入本模块或 SDK；``OpenSandboxProvider``
只依赖 ``OpenSandboxClient`` 协议，本类是唯一允许 import opensandbox 的实现。

安全立场：

- 镜像引用始终是 ``<runtime>@sha256:<digest>``；docker daemon 只能解析本地已锁定的镜像，
  不存在 tag 回退或构建期漂移。
- 创建时声明 deny-all egress 策略（无规则），配合服务端 dns+nft egress 模式（仅 dns 模式会放过
  直连 IP 的非 53 端口流量）与 drop_capabilities / no_new_privileges / pids_limit 加固；
  ``inspect_sandbox`` 用有界运行时探测验证 user、root 可写性、NoNewPrivs、CapEff、
  出站网络（非 53 端口 TCP + DNS 解析）、三项挂载的存在性与读写性、cgroup 内存上限，
  任何一项不符都 fail closed。
- ``root_read_only`` 的 V1 语义是「根文件系统对执行用户不可写」：官方 OpenSandbox docker
  后端不提供只读根挂载，等价保证由非 root sandbox 用户 + no_new_privileges +
  无 effective capabilities 提供，attestation 以实际探测结果为准（见 ARCHITECTURE.md）。
- Host 路径只存在于本模块的 ``mount_resolver`` 回调中；SDK 请求只携带受控 resource 引用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import timedelta

from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException
from opensandbox.models.execd import Execution, RunCommandOpts
from opensandbox.models.filesystem import WriteEntry
from opensandbox.models.sandboxes import Host, NetworkPolicy, Volume
from opensandbox.sandbox import Sandbox as SdkSandbox

from dataharness.sandbox import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    SandboxAttestation,
    SandboxCancelledError,
    SandboxLostError,
    SandboxPolicyError,
    SandboxSpec,
    SandboxTimeoutError,
)

logger = logging.getLogger(__name__)

#: 写入镜像的 SQL runner 绝对路径（Dockerfile 固定）。
_SQL_RUNNER = "/usr/local/bin/dataharness-sql-runner.py"
#: inspect 时运行探测命令的总时间预算。
_PROBE_TIMEOUT_SECONDS = 15
#: 探测脚本：以 Python heredoc 运行（无 shell 引号问题），输出 KEY=VALUE 行供 _parse_probe 解析。
_PROBE_SCRIPT = r"""
python - <<'PY'
import os
import pwd
import socket

lines = []
lines.append(f"USER={pwd.getpwuid(os.getuid()).pw_name}")
lines.append(f"UID={os.getuid()}")
with open("/proc/self/status", encoding="utf-8") as handle:
    status = handle.read()
no_new_privs = "1"
cap_eff = "0000000000000000"
for row in status.splitlines():
    if row.startswith("NoNewPrivs:"):
        no_new_privs = row.split(":", 1)[1].strip()
    elif row.startswith("CapEff:"):
        cap_eff = row.split(":", 1)[1].strip()
lines.append(f"NO_NEW_PRIVS={no_new_privs}")
lines.append(f"CAP_EFF={cap_eff}")
try:
    with open("/__dataharness_probe__", "w", encoding="utf-8") as handle:
        handle.write("x")
    lines.append("TOUCH_ROOT=writable")
except OSError:
    lines.append("TOUCH_ROOT=denied")
net_denied = True
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1)
    net_denied = False
except OSError:
    pass
try:
    socket.gethostbyname("example.com")
    net_denied = False
except OSError:
    pass
lines.append(f"NET_PROBE={'connected' if not net_denied else 'denied'}")
for name, label in (
    ("/project", "PROJECT"),
    ("/task/working", "WORKING"),
    ("/task/staging", "STAGING"),
):
    lines.append(f"MOUNT_{label}={'present' if os.path.isdir(name) else 'missing'}")
for name, label in (
    ("/project", "PROJECT"),
    ("/task/working", "WORKING"),
    ("/task/staging", "STAGING"),
):
    probe = os.path.join(name, "__dataharness_probe__")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("x")
        os.remove(probe)
        lines.append(f"WRITE_{label}=writable")
    except OSError:
        lines.append(f"WRITE_{label}=denied")
try:
    with open("/sys/fs/cgroup/memory.max", encoding="utf-8") as handle:
        lines.append(f"MEM_LIMIT={handle.read().strip()}")
except OSError:
    lines.append("MEM_LIMIT=unknown")
print("\n".join(lines))
PY
"""

#: metadata 键：创建时回写，重连时用于比对 image digest。服务端 metadata 值受
#: Kubernetes label 规则限制（<=63 字符），因此 digest 按 32+32 十六进制拆分存储。
_METADATA_DIGEST_HEAD = "dataharness.image_digest"
_METADATA_DIGEST_TAIL = "dataharness.image_digest_tail"
_METADATA_SNAPSHOT = "dataharness.project_snapshot_id"


def _digest_metadata(image_digest: str) -> dict[str, str]:
    hex_part = image_digest.removeprefix("sha256:")
    return {
        _METADATA_DIGEST_HEAD: hex_part[:32],
        _METADATA_DIGEST_TAIL: hex_part[32:],
    }


def _digest_from_metadata(metadata: Mapping[str, object] | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    head = metadata.get(_METADATA_DIGEST_HEAD)
    tail = metadata.get(_METADATA_DIGEST_TAIL)
    if not isinstance(head, str) or not isinstance(tail, str):
        return None
    return f"sha256:{head}{tail}"


class SdkOpenSandboxClient:
    """实现 ``OpenSandboxClient`` 协议的真实 SDK 包装。

    ``mount_resolver`` 是唯一的 Host 路径入口：把 ``SandboxMount.source_ref``（如
    ``snapshot:<id>``、``task:<id>:working``）解析为真实目录；上层与测试只提供逻辑引用。
    每个 sandbox 只连接一次并缓存；连接失败、服务不可达或 attestation 漂移都转为
    fail-closed 错误。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None = None,
        mount_resolver: Callable[[str], str],
        ready_timeout_seconds: int = 90,
        sandbox_timeout_minutes: int = 30,
    ) -> None:
        domain = endpoint
        protocol = "http"
        if "://" in endpoint:
            protocol, _, domain = endpoint.partition("://")
        self._connection = ConnectionConfig(
            domain=domain,
            protocol=protocol,
            api_key=api_key,
            request_timeout=timedelta(seconds=60),
        )
        self._mount_resolver = mount_resolver
        self._ready_timeout = timedelta(seconds=ready_timeout_seconds)
        self._sandbox_timeout = timedelta(minutes=sandbox_timeout_minutes)
        self._sandboxes: dict[str, SdkSandbox] = {}
        self._specs: dict[str, SandboxSpec] = {}
        self._executions: dict[tuple[str, str], str] = {}
        self._cancel_events: dict[tuple[str, str], asyncio.Event] = {}

    # ── 连接与生命周期 ──────────────────────────────────────────────────────

    async def _sandbox(self, sandbox_id: str) -> SdkSandbox:
        cached = self._sandboxes.get(sandbox_id)
        if cached is not None:
            return cached
        try:
            sandbox = await SdkSandbox.connect(
                sandbox_id,
                connection_config=self._connection,
                connect_timeout=self._ready_timeout,
            )
        except Exception as error:
            raise SandboxLostError("无法连接 OpenSandbox Sandbox") from error
        self._sandboxes[sandbox_id] = sandbox
        return sandbox

    async def create_sandbox(self, spec: SandboxSpec) -> str:
        """按 Spec 创建并等待就绪；docker daemon 只接受已锁定的 digest 镜像。"""
        image = f"{spec.runtime}@{spec.image_digest}"
        volumes = [
            Volume(
                name=f"dh-mount-{index}",
                mountPath=mount.target,
                host=Host(path=self._mount_resolver(mount.source_ref)),
                readOnly=mount.read_only,
            )
            for index, mount in enumerate(spec.mounts)
        ]
        resource: dict[str, str] = {"memory": f"{spec.resources.memory_mb}Mi"}
        if spec.resources.cpu_limit is not None:
            resource["cpu"] = f"{spec.resources.cpu_limit}"
        metadata = {
            **_digest_metadata(spec.image_digest),
            _METADATA_SNAPSHOT: str(spec.project_snapshot_id),
        }
        try:
            sandbox = await SdkSandbox.create(
                image,
                resource=resource,
                metadata=metadata,
                volumes=volumes,
                network_policy=NetworkPolicy(defaultAction="deny", egress=[]),
                timeout=self._sandbox_timeout,
                ready_timeout=self._ready_timeout,
                connection_config=self._connection,
            )
        except Exception as error:
            raise SandboxLostError("OpenSandbox 创建失败或就绪超时") from error
        self._sandboxes[sandbox.id] = sandbox
        self._specs[sandbox.id] = spec
        return sandbox.id

    async def terminate_sandbox(self, sandbox_id: str) -> None:
        """销毁远程 Sandbox 并关闭本地资源；失败仍清理本地缓存。"""
        sandbox = self._sandboxes.pop(sandbox_id, None)
        self._specs.pop(sandbox_id, None)
        self._executions = {
            key: value for key, value in self._executions.items() if key[0] != sandbox_id
        }
        self._cancel_events = {
            key: value for key, value in self._cancel_events.items() if key[0] != sandbox_id
        }
        if sandbox is None:
            return
        try:
            await sandbox.destroy()
        except Exception as error:
            raise SandboxLostError("OpenSandbox 销毁失败") from error

    # ── attestation ─────────────────────────────────────────────────────────

    async def inspect_sandbox(self, sandbox_id: str) -> SandboxAttestation:
        """从实际 Sandbox 读取并验证全部安全事实；任何漂移都 fail closed。"""
        spec = self._specs.get(sandbox_id)
        if spec is None:
            raise SandboxPolicyError("不能检查未由当前 Provider 创建并认证的 Sandbox")
        sandbox = await self._sandbox(sandbox_id)
        try:
            info = await sandbox.get_info()
        except Exception as error:
            raise SandboxLostError("无法读取 OpenSandbox Sandbox 信息") from error
        metadata = info.metadata if info.metadata is not None else {}
        if _digest_from_metadata(metadata) != spec.image_digest:
            raise SandboxPolicyError("OpenSandbox 元数据中的镜像 digest 与规格不一致")
        if _METADATA_SNAPSHOT in metadata and (
            str(metadata[_METADATA_SNAPSHOT]) != str(spec.project_snapshot_id)
        ):
            raise SandboxPolicyError("OpenSandbox 元数据中的 Snapshot 与规格不一致")
        uri = ""
        if info.image is not None:
            uri = getattr(info.image, "image", "") or getattr(info.image, "uri", "") or ""
        if spec.runtime not in uri:
            raise SandboxPolicyError("OpenSandbox 实际镜像与规格运行时不一致")

        probe_lines = await self._probe(sandbox)
        facts = _parse_probe(probe_lines)
        self._assert_probe_facts(spec, facts)

        resources = spec.resources
        mem_limit_raw = facts.get("mem_limit")
        if isinstance(mem_limit_raw, str) and mem_limit_raw not in ("unknown", "max"):
            parsed = _parse_memory_max(mem_limit_raw)
            if (
                parsed is not None
                and abs(parsed - spec.resources.memory_mb * 1024 * 1024)
                > spec.resources.memory_mb * 1024 * 1024 * 0.5
            ):
                raise SandboxPolicyError("OpenSandbox 内存上限与规格不一致")
        return SandboxAttestation(
            image_digest=spec.image_digest,
            network_enabled=not bool(facts["network_denied"]),
            privileged=not bool(facts["no_caps"]),
            root_read_only=bool(facts["root_read_only"]),
            user=spec.user,
            mounts=spec.mounts,
            resources=resources,
        )

    async def _probe(self, sandbox: SdkSandbox) -> str:
        """执行有界探测脚本；探测失败视为 Sandbox 丢失而非宽松通过。"""
        try:
            execution = await asyncio.wait_for(
                sandbox.commands.run(
                    _PROBE_SCRIPT,
                    opts=RunCommandOpts(timeout=timedelta(seconds=_PROBE_TIMEOUT_SECONDS)),
                ),
                timeout=_PROBE_TIMEOUT_SECONDS + 10,
            )
        except TimeoutError as error:
            raise SandboxLostError("OpenSandbox attestation 探测超时") from error
        except Exception as error:
            raise SandboxLostError("无法执行 OpenSandbox attestation 探测") from error
        return _execution_text(execution)

    @staticmethod
    def _assert_probe_facts(spec: SandboxSpec, facts: dict[str, str | bool]) -> None:
        """把探测事实与 Spec 的安全承诺逐项比对。"""
        user = facts.get("user", "")
        if user != spec.user:
            raise SandboxPolicyError(
                f"OpenSandbox 运行用户为 {user!r}，与规格 {spec.user!r} 不一致"
            )
        if facts.get("uid") in (None, "", "0"):
            raise SandboxPolicyError("OpenSandbox 必须以非 root 用户运行")
        if not facts.get("no_new_privs"):
            raise SandboxPolicyError("OpenSandbox 未启用 no_new_privileges")
        if not facts.get("no_caps"):
            raise SandboxPolicyError("OpenSandbox 进程仍持有 effective capabilities")
        if not facts.get("root_read_only"):
            raise SandboxPolicyError("OpenSandbox 根文件系统对执行用户可写")
        if facts.get("network_denied") is not True:
            raise SandboxPolicyError("OpenSandbox 出站网络未被拒绝")
        if facts.get("project_mounted") is not True:
            raise SandboxPolicyError("OpenSandbox 缺少 /project 挂载")
        if facts.get("working_mounted") is not True:
            raise SandboxPolicyError("OpenSandbox 缺少 /task/working 挂载")
        if facts.get("staging_mounted") is not True:
            raise SandboxPolicyError("OpenSandbox 缺少 /task/staging 挂载")
        if facts.get("project_writable") is not False:
            raise SandboxPolicyError("OpenSandbox 的 /project 必须只读")
        if facts.get("working_writable") is not True:
            raise SandboxPolicyError("OpenSandbox 的 /task/working 必须可写")
        if facts.get("staging_writable") is not True:
            raise SandboxPolicyError("OpenSandbox 的 /task/staging 必须可写")

    # ── Step 执行 ───────────────────────────────────────────────────────────

    async def execute_step(self, sandbox_id: str, request: ExecutionRequest) -> ExecutionResult:
        """把 code 写入当前 Task working 域，作为独立进程执行并映射结果。"""
        sandbox = await self._sandbox(sandbox_id)
        suffix = ".py" if request.kind.value != "SQL" else ".sql"
        code_path = f"/task/working/{request.step_id}{suffix}"
        schema_path = f"{code_path}.schema.json"
        if request.kind.value == "SQL":
            command = f"python {_SQL_RUNNER} {code_path}"
        else:
            command = f"python {code_path}"
        try:
            await sandbox.files.write_files(
                [WriteEntry(path=code_path, data=request.code, mode=0o644)]
            )
        except Exception as error:
            raise SandboxLostError("无法把 Step 代码写入 Sandbox") from error

        started = time.monotonic()
        cancel_event = asyncio.Event()
        self._cancel_events[(sandbox_id, str(request.step_id))] = cancel_event
        run_task: asyncio.Task[Execution] | None = None
        try:
            run_task = asyncio.create_task(
                sandbox.commands.run(
                    command,
                    opts=RunCommandOpts(
                        timeout=timedelta(seconds=request.timeout_seconds),
                        working_directory="/task/working",
                    ),
                )
            )
            cancel_waiter = asyncio.create_task(cancel_event.wait())
            done, pending = await asyncio.wait(
                (run_task, cancel_waiter),
                timeout=request.timeout_seconds + 30,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending)
            if cancel_event.is_set():
                raise SandboxCancelledError("OpenSandbox Step 已被取消")
            if run_task not in done:
                raise SandboxTimeoutError("OpenSandbox Step 执行超时")
            execution = run_task.result()
        except SandboxTimeoutError:
            raise
        except SandboxCancelledError:
            raise
        except SandboxException as error:
            message = str(error).lower()
            if "timeout" in message or "timed out" in message:
                raise SandboxTimeoutError("OpenSandbox Step 执行超时") from error
            if "cancel" in message or "interrupt" in message:
                raise SandboxCancelledError("OpenSandbox Step 已被取消") from error
            raise SandboxLostError("OpenSandbox Step 执行失败") from error
        except Exception as error:
            raise SandboxLostError("OpenSandbox Step 执行失败或 Sandbox 已丢失") from error
        finally:
            self._cancel_events.pop((sandbox_id, str(request.step_id)), None)

        duration_ms = int((time.monotonic() - started) * 1000)
        if execution.id:
            self._executions[(sandbox_id, str(request.step_id))] = execution.id
        status = _execution_status(execution, request.timeout_seconds)
        schema, statistics, resource_stats = await self._read_sidecar(sandbox, schema_path)
        stdout = _execution_text(execution)
        stderr = _execution_error_text(execution)
        return ExecutionResult(
            status=status,
            exit_code=execution.exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            schema=schema,
            statistics=statistics,
            resource_stats=resource_stats,
            process_id=execution.id,
        )

    async def _read_sidecar(
        self, sandbox: SdkSandbox, path: str
    ) -> tuple[dict[str, object], dict[str, int | float], dict[str, int | float]]:
        """读取 runner 写入的 schema/statistics sidecar；缺失或超限视为无附加事实。"""
        try:
            text = await sandbox.files.read_file(path, range_header="bytes=0-262143")
        except Exception:
            return {}, {}, {}
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return {}, {}, {}
        schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
        statistics = (
            payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {}
        )
        resource_stats = (
            payload.get("resource_stats") if isinstance(payload.get("resource_stats"), dict) else {}
        )
        return schema, statistics, resource_stats

    async def cancel_step(self, sandbox_id: str, step_id: str) -> None:
        """置位取消事件并中断已跟踪的 Step 执行；随后由调用方请求 cleanup。"""
        sandbox = await self._sandbox(sandbox_id)
        key = (sandbox_id, step_id)
        event = self._cancel_events.get(key)
        if event is not None:
            event.set()
        execution_id = self._executions.pop(key, None)
        if execution_id is None:
            return
        try:
            await sandbox.commands.interrupt(execution_id)
        except Exception as error:
            raise SandboxLostError("无法取消 OpenSandbox Step") from error

    async def cleanup_step(self, sandbox_id: str, step_id: str) -> None:
        """尽力清理 Step 残留：移除代码与 sidecar 文件，重试中断幂等清理。"""
        sandbox = await self._sandbox(sandbox_id)
        execution_id = self._executions.pop((sandbox_id, step_id), None)
        if execution_id is not None:
            with suppress(Exception):
                await sandbox.commands.interrupt(execution_id)
        for name in (f"{step_id}.py", f"{step_id}.sql"):
            with suppress(Exception):
                await sandbox.files.delete_files([f"/task/working/{name}"])
            with suppress(Exception):
                await sandbox.files.delete_files([f"/task/working/{name}.schema.json"])


# ── 纯函数映射（可单元测试） ────────────────────────────────────────────────


def _execution_text(execution: Execution) -> str:
    return execution.text


def _execution_error_text(execution: Execution) -> str:
    chunks = [message.text for message in execution.logs.stderr]
    return "\n".join(chunks)


def _execution_status(execution: Execution, timeout_seconds: int) -> ExecutionStatus:
    """把 SDK Execution 映射为稳定分类；超时/取消由退出码与错误文本识别。"""
    if execution.exit_code is None:
        if execution.error is not None:
            return ExecutionStatus.FAILED
        return ExecutionStatus.SUCCEEDED
    if execution.exit_code == 0:
        return ExecutionStatus.SUCCEEDED
    message = str(execution.error.value if execution.error else "").lower()
    stderr = _execution_error_text(execution).lower()
    if "timeout" in message or "timed out" in stderr or execution.exit_code == 137:
        return ExecutionStatus.TIMED_OUT
    if "cancel" in message or "interrupt" in stderr or execution.exit_code == 130:
        return ExecutionStatus.CANCELLED
    return ExecutionStatus.FAILED


def _parse_probe(output: str) -> dict[str, str | bool]:
    """解析探测输出；缺失的键以「无法证明」处理（fail closed）。"""
    raw: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            raw[key.strip()] = value.strip()
    cap_eff = raw.get("CAP_EFF", "")
    return {
        "user": raw.get("USER", ""),
        "uid": raw.get("UID", ""),
        "no_new_privs": raw.get("NO_NEW_PRIVS") == "1",
        "no_caps": cap_eff == "0000000000000000",
        "root_read_only": raw.get("TOUCH_ROOT", "denied") == "denied",
        "network_denied": "denied" in raw.get("NET_PROBE", ""),
        "project_mounted": raw.get("MOUNT_PROJECT") == "present",
        "working_mounted": raw.get("MOUNT_WORKING") == "present",
        "staging_mounted": raw.get("MOUNT_STAGING") == "present",
        "project_writable": raw.get("WRITE_PROJECT") != "denied",
        "working_writable": raw.get("WRITE_WORKING") != "denied",
        "staging_writable": raw.get("WRITE_STAGING") != "denied",
        "mem_limit": raw.get("MEM_LIMIT", "unknown"),
    }


def _parse_memory_max(value: str) -> int | None:
    """解析 cgroup v2 ``memory.max`` 字节数；``max``/非数字返回 None。"""
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
