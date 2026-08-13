"""OpenSandboxProvider 的 attestation、清理与 fail-closed 集成测试。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from dataharness.domain import ProjectId, RunId, SnapshotId, StepId, TaskId
from dataharness.providers.sandbox import OpenSandboxProvider
from dataharness.sandbox import (
    ExecutionKind,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    SandboxAttestation,
    SandboxMount,
    SandboxPolicyError,
    SandboxResources,
    SandboxSpec,
)

DIGEST = "sha256:" + "b" * 64


def _spec() -> SandboxSpec:
    return SandboxSpec(
        project_id=ProjectId("project-1"),
        task_id=TaskId("task-1"),
        run_id=RunId("run-1"),
        project_snapshot_id=SnapshotId("snapshot-1"),
        image_digest=DIGEST,
        mounts=(
            SandboxMount(source_ref="snapshot:snapshot-1", target="/project", read_only=True),
            SandboxMount(source_ref="task:task-1:working", target="/task/working", read_only=False),
            SandboxMount(source_ref="task:task-1:staging", target="/task/staging", read_only=False),
        ),
        resources=SandboxResources(
            memory_mb=128, disk_mb=256, max_output_bytes=16, step_timeout_seconds=5
        ),
    )


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        step_id=StepId("step-1"), kind=ExecutionKind.SQL, code="select 1", timeout_seconds=2
    )


@dataclass
class RecordingOpenSandboxClient:
    """SDK 包装层 fake：返回独立 attestation，并记录清理和销毁调用。"""

    actual: SandboxAttestation
    result: ExecutionResult = field(
        default_factory=lambda: ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=1,
        )
    )
    terminated: list[str] = field(default_factory=list)
    cleaned: list[tuple[str, str]] = field(default_factory=list)

    async def create_sandbox(self, spec: SandboxSpec) -> str:
        return "opensandbox-1"

    async def inspect_sandbox(self, sandbox_id: str) -> SandboxAttestation:
        return self.actual

    async def execute_step(self, sandbox_id: str, request: ExecutionRequest) -> ExecutionResult:
        return self.result

    async def cancel_step(self, sandbox_id: str, step_id: str) -> None:
        return None

    async def cleanup_step(self, sandbox_id: str, step_id: str) -> None:
        self.cleaned.append((sandbox_id, step_id))

    async def terminate_sandbox(self, sandbox_id: str) -> None:
        self.terminated.append(sandbox_id)


def _attestation(spec: SandboxSpec) -> SandboxAttestation:
    return SandboxAttestation(
        image_digest=spec.image_digest,
        network_enabled=spec.network_enabled,
        privileged=spec.privileged,
        root_read_only=spec.root_read_only,
        user=spec.user,
        mounts=spec.mounts,
        resources=spec.resources,
    )


@pytest.mark.asyncio
async def test_provider_attests_then_cleans_up_every_step() -> None:
    """适配层既不执行 SQL，也不保留后台进程；SDK cleanup 是 finally 中的不变量。"""
    spec = _spec()
    client = RecordingOpenSandboxClient(_attestation(spec))
    provider = OpenSandboxProvider(client)
    lease = await provider.create(spec)

    result = await provider.execute(lease, _request())

    assert result.stdout == "ok"
    assert client.cleaned == [("opensandbox-1", "step-1")]


@pytest.mark.asyncio
async def test_provider_fails_closed_and_terminates_on_attestation_drift() -> None:
    """任何网络、镜像、用户、资源或挂载漂移都不能获得 lease。"""
    spec = _spec()
    actual = _attestation(spec).model_copy(update={"network_enabled": True})
    client = RecordingOpenSandboxClient(actual)
    provider = OpenSandboxProvider(client)

    with pytest.raises(SandboxPolicyError):
        await provider.create(spec)
    assert client.terminated == ["opensandbox-1"]
