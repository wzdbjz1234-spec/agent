"""SandboxProvider 的公开行为契约，使用确定性 fake 不执行任何 Host 代码。"""

from __future__ import annotations

import pytest

from dataharness.domain import ProjectId, RunId, SnapshotId, StepId, TaskId
from dataharness.providers.sandbox import FakeExecutionPlan, FakeSandboxProvider
from dataharness.sandbox import (
    ExecutionKind,
    ExecutionRequest,
    ExecutionStatus,
    SandboxCancelledError,
    SandboxLostError,
    SandboxMount,
    SandboxOutputLimitError,
    SandboxResources,
    SandboxSpec,
    SandboxTimeoutError,
)

DIGEST = "sha256:" + "a" * 64


def _spec(run: str = "run-1", task: str = "task-1") -> SandboxSpec:
    """构造完全受控的 Run sandbox spec，不包含任何宿主路径或凭据。"""
    return SandboxSpec(
        project_id=ProjectId("project-1"),
        task_id=TaskId(task),
        run_id=RunId(run),
        project_snapshot_id=SnapshotId("snapshot-1"),
        image_digest=DIGEST,
        mounts=(
            SandboxMount(source_ref="snapshot:snapshot-1", target="/project", read_only=True),
            SandboxMount(
                source_ref=f"task:{task}:working", target="/task/working", read_only=False
            ),
            SandboxMount(
                source_ref=f"task:{task}:staging", target="/task/staging", read_only=False
            ),
        ),
        resources=SandboxResources(
            cpu_limit=1,
            memory_mb=256,
            disk_mb=512,
            max_processes=4,
            max_output_bytes=12,
            step_timeout_seconds=10,
        ),
    )


def _request(step: str = "step-1", *, timeout: int = 5) -> ExecutionRequest:
    return ExecutionRequest(
        step_id=StepId(step),
        kind=ExecutionKind.PYTHON,
        code="untrusted code is data, never Host-executed",
        timeout_seconds=timeout,
        expected_output_names=("result.csv",),
    )


@pytest.mark.asyncio
async def test_fake_contract_create_connect_execute_and_cleanup() -> None:
    """每个 Step 返回独立 process 标识，且结果路径始终调用 cleanup 记录。"""
    provider = FakeSandboxProvider()
    lease = await provider.create(_spec())
    assert await provider.connect(lease.sandbox_id) == lease

    result = await provider.execute(lease, _request())

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.process_id is not None
    assert provider.cleaned_steps == {(lease.sandbox_id, "step-1")}
    assert provider.received_code == ["untrusted code is data, never Host-executed"]


@pytest.mark.asyncio
async def test_fake_contract_timeout_loss_output_limit_and_cancel_are_cleaned() -> None:
    """所有非成功路径都没有残留 Step；结果不依赖 Sandbox 内存恢复。"""
    provider = FakeSandboxProvider()
    lease = await provider.create(_spec())
    provider.plan("timed", FakeExecutionPlan(timeout=True))
    provider.plan("lost", FakeExecutionPlan(sandbox_lost=True))
    provider.plan("large", FakeExecutionPlan(stdout="x" * 13))

    with pytest.raises(SandboxTimeoutError):
        await provider.execute(lease, _request("timed"))
    with pytest.raises(SandboxOutputLimitError):
        await provider.execute(lease, _request("large"))
    await provider.cancel(lease, "cancelled")
    with pytest.raises(SandboxCancelledError):
        await provider.execute(lease, _request("cancelled"))
    with pytest.raises(SandboxLostError):
        await provider.execute(lease, _request("lost"))

    assert (lease.sandbox_id, "timed") in provider.cleaned_steps
    assert (lease.sandbox_id, "large") in provider.cleaned_steps
    assert (lease.sandbox_id, "cancelled") in provider.cleaned_steps
    assert (lease.sandbox_id, "lost") in provider.cleaned_steps


@pytest.mark.asyncio
async def test_terminating_one_parallel_run_does_not_affect_another() -> None:
    """同一 Project 的两个 Run 获得独立 lease；销毁一方不改变另一方。"""
    provider = FakeSandboxProvider()
    first = await provider.create(_spec("run-1", "task-1"))
    second = await provider.create(_spec("run-2", "task-2"))

    await provider.terminate(first)

    with pytest.raises(SandboxLostError):
        await provider.connect(first.sandbox_id)
    assert (await provider.execute(second, _request("second"))).status == ExecutionStatus.SUCCEEDED
