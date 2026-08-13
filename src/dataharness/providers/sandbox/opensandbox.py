"""OpenSandbox SDK 的受控适配层。

这里的 ``OpenSandboxClient`` 是对 SDK 的窄包装：部署装配层可以将官方 SDK 的创建、检查、
执行、取消和销毁操作映射到此协议，而上层永远不导入 SDK，也不接触任何宿主路径。适配器在
每次创建/重连后重新检查 attestation；SDK 缺失、服务不可达或配置漂移均失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dataharness.sandbox import (
    ExecutionRequest,
    ExecutionResult,
    SandboxAttestation,
    SandboxCancelledError,
    SandboxLease,
    SandboxLostError,
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxSpec,
    SandboxTimeoutError,
)


class OpenSandboxClient(Protocol):
    """官方 SDK 的最小包装协议；仅 Provider 层可以实现或注入它。"""

    async def create_sandbox(self, spec: SandboxSpec) -> str: ...

    async def inspect_sandbox(self, sandbox_id: str) -> SandboxAttestation: ...

    async def execute_step(self, sandbox_id: str, request: ExecutionRequest) -> ExecutionResult: ...

    async def cancel_step(self, sandbox_id: str, step_id: str) -> None: ...

    async def cleanup_step(self, sandbox_id: str, step_id: str) -> None: ...

    async def terminate_sandbox(self, sandbox_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _LeaseState:
    """Provider 私有状态，将已认证 Spec 与不透明 ID 绑定。"""

    lease: SandboxLease
    spec: SandboxSpec


class OpenSandboxProvider:
    """生产 OpenSandbox Provider：严格认证而非在 Host 上执行回退。

    适配器不解释 ``request.code``，也不包含 subprocess、exec、eval 或 shell 调用。实际代码
    只能作为 SDK ``execute_step`` 的载荷进入已认证 Sandbox。每次 Step 结束都运行 cleanup，
    即便超时、取消、Sandbox 丢失或 SDK 抛出异常也不会跳过残留进程回收。
    """

    def __init__(self, client: OpenSandboxClient) -> None:
        self._client = client
        self._leases: dict[str, _LeaseState] = {}
        self._active_steps: set[tuple[str, str]] = set()

    @staticmethod
    def _matches(spec: SandboxSpec, actual: SandboxAttestation) -> bool:
        """比较所有可影响隔离边界的字段；缺失或额外挂载均视为不可信。"""
        return (
            actual.image_digest == spec.image_digest
            and actual.network_enabled == spec.network_enabled
            and actual.privileged == spec.privileged
            and actual.root_read_only == spec.root_read_only
            and actual.user == spec.user
            and actual.mounts == spec.mounts
            and actual.resources == spec.resources
        )

    async def _attest(self, sandbox_id: str, spec: SandboxSpec) -> None:
        """从实际运行时重新读取配置；认证失败后调用方必须销毁或重建。"""
        try:
            actual = await self._client.inspect_sandbox(sandbox_id)
        except Exception as error:
            raise SandboxLostError("无法读取 OpenSandbox attestation") from error
        if not self._matches(spec, actual):
            raise SandboxPolicyError("OpenSandbox attestation 与请求的安全规格不一致")

    @staticmethod
    def _lease(sandbox_id: str, spec: SandboxSpec) -> SandboxLease:
        """创建只含稳定 ID 与安全事实的上层 lease。"""
        return SandboxLease(
            sandbox_id=sandbox_id,
            run_id=spec.run_id,
            task_id=spec.task_id,
            project_id=spec.project_id,
            project_snapshot_id=spec.project_snapshot_id,
            image_digest=spec.image_digest,
        )

    async def create(self, spec: SandboxSpec) -> SandboxLease:
        """创建后立即认证；认证失败时尽力销毁，绝不保留宽松 lease。"""
        try:
            sandbox_id = await self._client.create_sandbox(spec)
        except Exception as error:
            raise SandboxLostError("OpenSandbox 创建失败") from error
        try:
            await self._attest(sandbox_id, spec)
        except Exception:
            try:
                await self._client.terminate_sandbox(sandbox_id)
            finally:
                raise
        lease = self._lease(sandbox_id, spec)
        self._leases[sandbox_id] = _LeaseState(lease, spec)
        return lease

    async def connect(self, sandbox_id: str) -> SandboxLease:
        """仅重连本 Provider 已创建的 lease，防止未知 Sandbox 注入到 Run。"""
        state = self._leases.get(sandbox_id)
        if state is None:
            raise SandboxPolicyError("不能连接未由当前 Provider 创建并认证的 Sandbox")
        await self._attest(sandbox_id, state.spec)
        return state.lease

    def _require_active(self, lease: SandboxLease) -> _LeaseState:
        """验证调用方未伪造、过期或跨 Run 复用 lease。"""
        state = self._leases.get(lease.sandbox_id)
        if state is None:
            raise SandboxLostError("Sandbox lease 已终止或丢失")
        if state.lease != lease:
            raise SandboxPolicyError("Sandbox lease 与已认证 Run 不匹配")
        return state

    @staticmethod
    def _check_request(spec: SandboxSpec, request: ExecutionRequest) -> None:
        """请求不可放宽 Spec 限制，输出仅能通过预定义 staging 名称落盘。"""
        if request.timeout_seconds > spec.resources.step_timeout_seconds:
            raise SandboxPolicyError("Step 超时不能超过 Sandbox Spec 上限")

    async def execute(self, lease: SandboxLease, request: ExecutionRequest) -> ExecutionResult:
        """调用 SDK 独立 Step 进程，并在所有退出路径执行残留进程清理。"""
        state = self._require_active(lease)
        self._check_request(state.spec, request)
        key = (lease.sandbox_id, str(request.step_id))
        if key in self._active_steps:
            raise SandboxPolicyError("同一 Sandbox 不能并发执行相同 Step")
        self._active_steps.add(key)
        try:
            result = await self._client.execute_step(lease.sandbox_id, request)
            output_size = len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8"))
            if output_size > state.spec.resources.max_output_bytes:
                raise SandboxOutputLimitError("Sandbox 输出超过上限")
            return result
        except (
            SandboxCancelledError,
            SandboxLostError,
            SandboxOutputLimitError,
            SandboxTimeoutError,
        ):
            raise
        except TimeoutError as error:
            raise SandboxTimeoutError("OpenSandbox Step 超时") from error
        except Exception as error:
            raise SandboxLostError("OpenSandbox Step 执行失败或 Sandbox 已丢失") from error
        finally:
            self._active_steps.discard(key)
            try:
                await self._client.cleanup_step(lease.sandbox_id, str(request.step_id))
            except Exception as error:
                self._leases.pop(lease.sandbox_id, None)
                raise SandboxLostError("无法确认 Sandbox Step 残留进程已清理") from error

    async def cancel(self, lease: SandboxLease, step_id: str) -> None:
        """取消仅作用于当前 lease 的 Step，随后无条件请求 cleanup。"""
        self._require_active(lease)
        try:
            await self._client.cancel_step(lease.sandbox_id, step_id)
        except Exception as error:
            raise SandboxLostError("无法取消 OpenSandbox Step") from error
        finally:
            try:
                await self._client.cleanup_step(lease.sandbox_id, step_id)
            except Exception as error:
                self._leases.pop(lease.sandbox_id, None)
                raise SandboxLostError("取消后无法清理 Sandbox Step") from error

    async def terminate(self, lease: SandboxLease) -> None:
        """销毁当前 Run 的单个 Sandbox；其他 Run 的 lease 不在此路径中。"""
        self._require_active(lease)
        try:
            await self._client.terminate_sandbox(lease.sandbox_id)
        except Exception as error:
            raise SandboxLostError("OpenSandbox 销毁失败") from error
        finally:
            self._leases.pop(lease.sandbox_id, None)
            self._active_steps = {
                item for item in self._active_steps if item[0] != lease.sandbox_id
            }
