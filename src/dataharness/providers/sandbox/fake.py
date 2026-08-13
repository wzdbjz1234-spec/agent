"""确定性 fake SandboxProvider；从不在 Host 上执行请求代码。"""

from __future__ import annotations

from dataclasses import dataclass

from dataharness.sandbox import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    SandboxCancelledError,
    SandboxLease,
    SandboxLostError,
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxSpec,
    SandboxTimeoutError,
)


@dataclass(frozen=True, slots=True)
class FakeExecutionPlan:
    """按 Step ID 预置的确定性结果；``code`` 永远只作为查表输入，不会被执行。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 1
    timeout: bool = False
    sandbox_lost: bool = False


class FakeSandboxProvider:
    """测试 Provider，复现正式协议的 lease、隔离、取消、上限与清理语义。"""

    def __init__(self) -> None:
        self._leases: dict[str, tuple[SandboxLease, SandboxSpec]] = {}
        self._plans: dict[str, FakeExecutionPlan] = {}
        self._cancelled: set[tuple[str, str]] = set()
        self.cleaned_steps: set[tuple[str, str]] = set()
        self.received_code: list[str] = []
        self._sequence = 0

    def plan(self, step_id: str, result: FakeExecutionPlan) -> None:
        """注册单个 Step 的预期结果；未注册时返回空成功结果。"""
        self._plans[step_id] = result

    async def create(self, spec: SandboxSpec) -> SandboxLease:
        """为每次调用创建独立 lease，模拟同 Project 并行 Run 的隔离。"""
        self._sequence += 1
        sandbox_id = f"fake-sandbox-{self._sequence}"
        lease = SandboxLease(
            sandbox_id=sandbox_id,
            run_id=spec.run_id,
            task_id=spec.task_id,
            project_id=spec.project_id,
            project_snapshot_id=spec.project_snapshot_id,
            image_digest=spec.image_digest,
        )
        self._leases[sandbox_id] = (lease, spec)
        return lease

    async def connect(self, sandbox_id: str) -> SandboxLease:
        """只允许连接当前 fake 创建的活跃 lease。"""
        if sandbox_id not in self._leases:
            raise SandboxLostError("fake Sandbox 不存在")
        return self._leases[sandbox_id][0]

    def _active(self, lease: SandboxLease) -> SandboxSpec:
        item = self._leases.get(lease.sandbox_id)
        if item is None:
            raise SandboxLostError("fake Sandbox 已销毁")
        if item[0] != lease:
            raise SandboxPolicyError("fake lease 不属于当前 Run")
        return item[1]

    async def execute(self, lease: SandboxLease, request: ExecutionRequest) -> ExecutionResult:
        """返回预置结果并在 finally 记录清理；不会调用 exec/subprocess/eval。"""
        spec = self._active(lease)
        if request.timeout_seconds > spec.resources.step_timeout_seconds:
            raise SandboxPolicyError("Step 超时超过 Spec 上限")
        key = (lease.sandbox_id, str(request.step_id))
        self.received_code.append(request.code)
        try:
            if key in self._cancelled:
                raise SandboxCancelledError("fake Step 已取消")
            plan = self._plans.get(str(request.step_id), FakeExecutionPlan())
            if plan.sandbox_lost:
                self._leases.pop(lease.sandbox_id, None)
                raise SandboxLostError("fake Sandbox 丢失")
            if plan.timeout:
                raise SandboxTimeoutError("fake Step 超时")
            size = len(plan.stdout.encode("utf-8")) + len(plan.stderr.encode("utf-8"))
            if size > spec.resources.max_output_bytes:
                raise SandboxOutputLimitError("fake 输出超过上限")
            return ExecutionResult(
                status=ExecutionStatus.SUCCEEDED if plan.exit_code == 0 else ExecutionStatus.FAILED,
                exit_code=plan.exit_code,
                stdout=plan.stdout,
                stderr=plan.stderr,
                duration_ms=plan.duration_ms,
                process_id=f"fake-process-{lease.sandbox_id}-{request.step_id}",
            )
        finally:
            self.cleaned_steps.add(key)

    async def cancel(self, lease: SandboxLease, step_id: str) -> None:
        """仅标记本 lease 的目标 Step，互不影响其他 Sandbox。"""
        self._active(lease)
        key = (lease.sandbox_id, step_id)
        self._cancelled.add(key)
        self.cleaned_steps.add(key)

    async def terminate(self, lease: SandboxLease) -> None:
        """销毁单一 fake lease，不遍历或影响其他并行 Run。"""
        self._active(lease)
        self._leases.pop(lease.sandbox_id, None)
