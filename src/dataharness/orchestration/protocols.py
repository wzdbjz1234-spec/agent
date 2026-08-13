"""编排层与 Agent/Sandbox 的小型协议。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from dataharness.domain import Run
from dataharness.sandbox import SandboxLease, SandboxSpec

from .models import RecoveryDecision, RunOutcome


class RunExecutionContext:
    """一次 handler 调用的不可变执行上下文。

    ``run.project_snapshot_id`` 是唯一输入视图；即使 Project 在运行期间产生新版本，
    handler 也拿不到“最新 Project”替代它。Sandbox lease 只作为上层不透明值对象传递。
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
        now: datetime,
    ) -> None:
        self.run = run
        self.worker_owner = worker_owner
        self.lease_epoch = lease_epoch
        self.recovered = recovered
        self.recovery_decision = recovery_decision
        self.checkpoint_ref = checkpoint_ref
        self.sandbox_lease = sandbox_lease
        self.now = now


class RunHandler(Protocol):
    """可由 Agent facade 或测试 fake 实现的单 Run handler。"""

    async def __call__(self, context: RunExecutionContext) -> RunOutcome: ...


SandboxSpecFactory = Callable[[Run], SandboxSpec]
