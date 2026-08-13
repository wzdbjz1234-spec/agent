"""上层依赖的 SandboxProvider 小型异步协议。"""

from __future__ import annotations

from typing import Protocol

from .models import ExecutionRequest, ExecutionResult, SandboxLease, SandboxSpec


class SandboxProvider(Protocol):
    """Run-scoped Sandbox 生命周期；不提供 Host shell、装包或网络开关。"""

    async def create(self, spec: SandboxSpec) -> SandboxLease:
        """按精确安全规格创建并认证 Sandbox。"""
        ...

    async def connect(self, sandbox_id: str) -> SandboxLease:
        """重连已知 lease，并重新认证实际运行配置。"""
        ...

    async def execute(self, lease: SandboxLease, request: ExecutionRequest) -> ExecutionResult:
        """在独立 Step 进程执行不可信 Python/SQL/Skill 载荷。"""
        ...

    async def cancel(self, lease: SandboxLease, step_id: str) -> None:
        """取消当前 Step 并请求清理残留进程。"""
        ...

    async def terminate(self, lease: SandboxLease) -> None:
        """销毁一个 Run 的 Sandbox，不影响其他 lease。"""
        ...
