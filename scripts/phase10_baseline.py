"""Phase 10 可复现的轻量性能基线。

基线不调用真实云模型；模型出口使用 fake cloud，计算 Step 使用 fake Sandbox，因此不会
把网络、账号或宿主执行时间混入结果。真实 OpenSandbox 的启动/恢复耗时另由 live 测试
记录，运行本脚本会输出 JSON，便于 CI 或运维保存本次环境的数值。
"""

from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from dataharness.analysis import AnalysisRuntime
from dataharness.api import ApiService
from dataharness.config import PathsConfig, Settings
from dataharness.domain import Project, ProjectId, ProjectSnapshot, SnapshotId, TaskId
from dataharness.idgen import DeterministicIdFactory
from dataharness.orchestration import (
    ExecutionDecision,
    HostCrashError,
    RunOutcome,
    RunService,
    TaskService,
)
from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy
from dataharness.providers.durable import LocalDurableExecutor
from dataharness.providers.sandbox import FakeSandboxProvider
from dataharness.sandbox import SandboxMount, SandboxResources, SandboxSpec
from dataharness.storage import (
    PrivacyConnectionFactory,
    RuntimeConnectionFactory,
    SqliteRuntimeStore,
)
from dataharness.testing import FakeClock

DIGEST = "sha256:" + "f" * 64
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeCloud:
    """只记录已脱敏请求，不产生真实网络调用。"""

    def complete(self, request: str) -> str:
        return request


def _summary(values: list[float]) -> dict[str, float]:
    """以 p50/p95 输出少量稳定指标，避免报告依赖单次偶然值。"""
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[p95_index] * 1000, 3),
    }


def _measure(action, count: int = 5) -> dict[str, float]:
    """测量同步动作；调用方负责保证动作本身不含外部网络。"""
    values: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        action()
        values.append(time.perf_counter() - started)
    return _summary(values)


async def _measure_step(
    service: ApiService, project_id: ProjectId, snapshot_id: SnapshotId
) -> dict[str, float]:
    """测量 fake Sandbox 的 Step 启动到摘要落盘，不在 Host 执行代码。"""
    task = service.tasks.create(project_id)
    run = service.runs.create(task.id, snapshot_id)
    provider = FakeSandboxProvider()
    spec = SandboxSpec(
        project_id=project_id,
        task_id=task.id,
        run_id=run.id,
        project_snapshot_id=snapshot_id,
        image_digest=DIGEST,
        mounts=(
            SandboxMount(source_ref=f"snapshot:{snapshot_id}", target="/project", read_only=True),
            SandboxMount(
                source_ref=f"task:{task.id}:working", target="/task/working", read_only=False
            ),
            SandboxMount(
                source_ref=f"task:{task.id}:staging", target="/task/staging", read_only=False
            ),
        ),
        resources=SandboxResources(
            memory_mb=128,
            disk_mb=256,
            max_processes=4,
            max_output_bytes=1024,
            step_timeout_seconds=10,
        ),
    )
    lease = await provider.create(spec)
    runtime = AnalysisRuntime(
        service.store,
        service.corpus,
        service.workspace,
        provider,
        lease,
        id_factory=DeterministicIdFactory("baseline"),
        bridge=service.bridge,
    )
    try:
        values: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            await runtime.execute_python("print('baseline')", timeout_seconds=5)
            values.append(time.perf_counter() - started)
        return _summary(values)
    finally:
        await provider.terminate(lease)


async def _measure_recovery(tmp_root: Path) -> dict[str, float]:
    """测量同一 Run 在一次 HostCrash 后被新 worker 恢复的时间。"""
    clock = FakeClock(T0)
    store = SqliteRuntimeStore(RuntimeConnectionFactory(tmp_root / "recovery.db"))
    project = Project(id=ProjectId("baseline-project"), name="baseline", created_at=T0)
    snapshot = ProjectSnapshot(
        id=SnapshotId("baseline-snapshot"), project_id=project.id, created_at=T0
    )
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_snapshot(snapshot)
    task = TaskService(
        store, id_factory=DeterministicIdFactory("baseline-task"), clock=clock.now
    ).create(project.id)
    run = RunService(
        store, id_factory=DeterministicIdFactory("baseline-run"), clock=clock.now
    ).create(task.id, snapshot.id)
    calls = 0

    async def handler(_context) -> RunOutcome:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HostCrashError("synthetic baseline crash")
        return RunOutcome(decision=ExecutionDecision.SUCCEEDED)

    first_worker = LocalDurableExecutor(
        store, handler, owner="baseline-a", clock=clock.now, backoff_base=0
    )
    second_worker = LocalDurableExecutor(
        store, handler, owner="baseline-b", clock=clock.now, backoff_base=0
    )
    started = time.perf_counter()
    await first_worker.run_once()
    result = await second_worker.run_once()
    assert result is not None and str(result.id) == str(run.id)
    return _summary([time.perf_counter() - started])


def main() -> int:
    """输出本次环境的性能和资源上限基线。"""
    with tempfile.TemporaryDirectory(prefix="dataharness-phase10-") as directory:
        root = Path(directory)
        settings = Settings(paths=PathsConfig(runtime_data_root=root / "runtime-data"))
        service = ApiService.from_settings(settings)
        project = service.corpus.create_project("phase10-baseline")
        cloud = ModelGateway(
            FakeCloud(),
            PrivacyPolicy(
                PlaceholderStore(
                    PrivacyConnectionFactory(root / "privacy", service.store.factory.path)
                )
            ),
        )
        privacy = _measure(
            lambda: cloud.complete(TaskId("baseline-task"), "业务 alice@example.test")
        )
        import_latency = _measure(
            lambda: service.import_file_bytes(project.id, "input.txt", b"baseline data")
        )
        snapshot = service.corpus.create_snapshot(project.id)
        step = asyncio.run(_measure_step(service, project.id, snapshot.id))
        recovery = asyncio.run(_measure_recovery(root))
        print(
            json.dumps(
                {
                    "environment": {"python": "3.12", "fake_cloud": True, "fake_sandbox": True},
                    "latency_ms": {
                        "model_boundary_scan": privacy,
                        "file_import": import_latency,
                        "step_start_and_summary": step,
                        "durable_recovery": recovery,
                    },
                    "resource_limits": {
                        "max_file_bytes": settings.extraction.max_file_bytes,
                        "max_output_bytes": settings.resources.max_output_bytes,
                        "memory_mb": settings.resources.memory_mb,
                        "disk_mb": settings.resources.disk_mb,
                        "max_processes": settings.resources.max_processes,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
