"""编排层与 Agent/Sandbox 的小型协议。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from dataharness.domain import Run
from dataharness.sandbox import SandboxLease, SandboxSpec

from .models import RecoveryDecision, RunOutcome


class RunExecutionContext:
    """一次 handler 调用的固定 Run 上下文与按需 Sandbox seam。

    ``run.project_snapshot_id`` 是唯一输入视图；即使 Project 在运行期间产生新版本，
    handler 也拿不到“最新 Project”替代它。Sandbox lease 可为空，执行型工具通过
    ``ensure_sandbox`` 取得并缓存；因此只读问题不会被隔离环境启动副作用拖慢。
    """

    def __init__(
        self,
        run: Run,
        *,
        worker_owner: str,
        lease_epoch: int,
        recovered: bool,
        recovery_decision: RecoveryDecision,
        checkpoint_ref: str | None,
        sandbox_lease: SandboxLease | None,
        sandbox_factory: Callable[[], Awaitable[SandboxLease]] | None = None,
        now: datetime,
    ) -> None:
        self.run = run
        self.worker_owner = worker_owner
        self.lease_epoch = lease_epoch
        self.recovered = recovered
        self.recovery_decision = recovery_decision
        self.checkpoint_ref = checkpoint_ref
        self.sandbox_lease = sandbox_lease
        self._sandbox_factory = sandbox_factory
        self.now = now

    async def ensure_sandbox(self) -> SandboxLease:
        """按需取得当前 Run 的 Sandbox，并在上下文中缓存 lease。

        普通对话、项目元数据检索和文件检查不需要隔离执行环境；只有执行工具
        真正调用此方法时，durable executor 才会创建或恢复 Sandbox。
        """
        if self.sandbox_lease is not None:
            return self.sandbox_lease
        if self._sandbox_factory is None:
            raise RuntimeError("当前 Run 未配置 Sandbox 按需创建器")
        self.sandbox_lease = await self._sandbox_factory()
        return self.sandbox_lease


class RunHandler(Protocol):
    """可由 Agent facade 或测试 fake 实现的单 Run handler。"""

    async def __call__(self, context: RunExecutionContext) -> RunOutcome: ...


SandboxSpecFactory = Callable[[Run], SandboxSpec]
