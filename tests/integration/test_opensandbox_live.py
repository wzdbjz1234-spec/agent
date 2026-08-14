"""真实 OpenSandbox 服务 + secure-analysis 镜像的集成测试（需 Docker + 本地服务）。

运行条件：
- OpenSandbox 服务运行在 127.0.0.1:8080（docker runtime）
- secure-analysis 镜像已按 build.ps1 构建并锁定 digest（build-evidence/image-digest.txt）
- 设置环境变量 ``DATAHARNESS_LIVE_SANDBOX=1`` 才执行；否则显式跳过（不掩盖失败）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dataharness.analysis import AnalysisRuntime, OutputSpec
from dataharness.domain import (
    ContentHash,
    Project,
    ProjectId,
    ProjectSnapshot,
    Run,
    RunId,
    SnapshotId,
    StepId,
    Task,
    TaskId,
    WaitReason,
)
from dataharness.idgen import DeterministicIdFactory
from dataharness.orchestration import (
    ExecutionDecision,
    RecoveryDecision,
    RunExecutionContext,
    RunOutcome,
    RunService,
)
from dataharness.projects import ProjectCorpus
from dataharness.providers.durable import LocalDurableExecutor
from dataharness.providers.sandbox import OpenSandboxProvider
from dataharness.providers.sandbox.opensandbox_sdk import SdkOpenSandboxClient
from dataharness.providers.workspace import FakeWorkspace, LocalWorkspace
from dataharness.sandbox import (
    ExecutionKind,
    ExecutionRequest,
    ExecutionStatus,
    SandboxCancelledError,
    SandboxLostError,
    SandboxMount,
    SandboxResources,
    SandboxSpec,
)
from dataharness.storage import (
    CheckpointMetadata,
    RuntimeConnectionFactory,
    SqlitePublicationJournal,
    SqliteRuntimeStore,
)
from dataharness.workspace import PublicationKind, WorkspaceBridge

LIVE = os.environ.get("DATAHARNESS_LIVE_SANDBOX") == "1"
REQUIRES_LIVE = pytest.mark.skipif(
    not LIVE,
    reason="设置 DATAHARNESS_LIVE_SANDBOX=1 并启动 OpenSandbox 服务后运行真实集成测试",
)

ENDPOINT = os.environ.get("OPEN_SANDBOX_ENDPOINT", "http://127.0.0.1:8080")
_MOUNT_ROOT = Path("runtime-data") / "live-sandbox"


def _locked_digest() -> str:
    evidence = Path("sandbox-images/secure-analysis/build-evidence/image-digest.txt")
    if not evidence.is_file():
        raise RuntimeError("缺少镜像锁定证据；请先运行 sandbox-images/secure-analysis/build.ps1")
    digest = evidence.read_text(encoding="utf-8").strip()
    assert digest.startswith("sha256:") and len(digest) == 71
    return digest


class LiveSandboxFixture:
    """真实 Sandbox 的组合 fixture：mount 根、provider、spec 与 resolver。"""

    def __init__(self) -> None:
        self.root = _MOUNT_ROOT / uuid.uuid4().hex
        self.project_dir = self.root / "project"
        self.working_dir = self.root / "working"
        self.staging_dir = self.root / "staging"
        for path in (self.project_dir, self.working_dir, self.staging_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.digest = _locked_digest()
        self.project_id = ProjectId("live-project")
        self.task_id = TaskId("live-task")
        self.run_id = RunId("live-run")
        self.snapshot_id = SnapshotId("live-snapshot")
        self.spec = self._make_spec(self.project_id, self.snapshot_id)

    def _make_spec(self, project_id: ProjectId, snapshot_id: SnapshotId) -> SandboxSpec:
        return SandboxSpec(
            project_id=project_id,
            task_id=self.task_id,
            run_id=self.run_id,
            project_snapshot_id=snapshot_id,
            image_digest=self.digest,
            mounts=(
                SandboxMount(
                    source_ref=f"snapshot:{snapshot_id}", target="/project", read_only=True
                ),
                SandboxMount(
                    source_ref=f"task:{self.task_id}:working",
                    target="/task/working",
                    read_only=False,
                ),
                SandboxMount(
                    source_ref=f"task:{self.task_id}:staging",
                    target="/task/staging",
                    read_only=False,
                ),
            ),
            resources=SandboxResources(
                memory_mb=1024,
                disk_mb=2048,
                max_processes=32,
                max_output_bytes=1_000_000,
                step_timeout_seconds=60,
            ),
        )

    def resolver(self, source_ref: str) -> str:
        """部署装配示例：snapshot 引用 → /project 目录；task 引用 → working/staging。"""
        if source_ref.startswith("snapshot:"):
            return str(self.project_dir.resolve())
        if source_ref == f"task:{self.task_id}:working":
            return str(self.working_dir.resolve())
        if source_ref == f"task:{self.task_id}:staging":
            return str(self.staging_dir.resolve())
        raise AssertionError(f"未知 mount 引用 {source_ref}")

    def provider(self) -> OpenSandboxProvider:
        client = SdkOpenSandboxClient(
            endpoint=ENDPOINT,
            mount_resolver=self.resolver,
            ready_timeout_seconds=120,
        )
        return OpenSandboxProvider(client)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture
def live() -> Generator[LiveSandboxFixture, None, None]:
    fixture = LiveSandboxFixture()
    yield fixture
    fixture.cleanup()


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_create_attest_execute_terminate(live: LiveSandboxFixture) -> None:
    """真实 create -> attestation -> python 执行 -> terminate 全链路。"""
    provider = live.provider()
    lease = await provider.create(live.spec)
    assert lease.image_digest == live.digest

    result = await provider.execute(
        lease,
        ExecutionRequest(
            step_id=StepId("step-hello"),
            kind=ExecutionKind.PYTHON,
            code="print('hello from sandbox')",
            timeout_seconds=20,
        ),
    )
    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.exit_code == 0
    assert "hello from sandbox" in result.stdout

    # 同一 digest 可重建
    await provider.terminate(lease)
    lease2 = await provider.create(live.spec)
    result2 = await provider.execute(
        lease2,
        ExecutionRequest(
            step_id=StepId("step-again"),
            kind=ExecutionKind.PYTHON,
            code="print(6 * 7)",
            timeout_seconds=20,
        ),
    )
    assert "42" in result2.stdout
    await provider.terminate(lease2)


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_sql_runner_reads_project_tables(live: LiveSandboxFixture) -> None:
    """真实 SQL runner 在 /project 上执行 DuckDB 查询并返回 schema/统计。"""
    (live.project_dir / "data.csv").write_text(
        "id,name\n1,alpha\n2,beta\n3,gamma\n", encoding="utf-8"
    )
    provider = live.provider()
    lease = await provider.create(live.spec)
    result = await provider.execute(
        lease,
        ExecutionRequest(
            step_id=StepId("step-sql"),
            kind=ExecutionKind.SQL,
            code='SELECT count(*) AS n FROM "data"',
            timeout_seconds=30,
        ),
    )
    assert result.status == ExecutionStatus.SUCCEEDED
    assert "3" in result.stdout
    assert result.statistics.get("rows") == 1
    columns = result.output_schema.get("columns", [])
    assert isinstance(columns, list) and any(
        isinstance(column, dict) and column.get("name") == "n" for column in columns
    )
    await provider.terminate(lease)


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_cancel_interrupts_running_step(live: LiveSandboxFixture) -> None:
    """取消真实运行中的 Step；execute 返回 CANCELLED 且 Sandbox 仍可继续使用。"""
    provider = live.provider()
    lease = await provider.create(live.spec)
    request = ExecutionRequest(
        step_id=StepId("step-sleep"),
        kind=ExecutionKind.PYTHON,
        code="import time; time.sleep(60)",
        timeout_seconds=60,
    )
    task = asyncio.create_task(provider.execute(lease, request))
    await asyncio.sleep(5)
    await provider.cancel(lease, str(request.step_id))
    with pytest.raises(SandboxCancelledError):
        await task

    result = await provider.execute(
        lease,
        ExecutionRequest(
            step_id=StepId("step-after-cancel"),
            kind=ExecutionKind.PYTHON,
            code="print('still alive')",
            timeout_seconds=20,
        ),
    )
    assert result.status == ExecutionStatus.SUCCEEDED
    await provider.terminate(lease)


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_parallel_runs_are_isolated(live: LiveSandboxFixture) -> None:
    """同一 Project 的两个并行 Run 使用独立 lease，销毁一个不影响另一个。"""
    provider = live.provider()
    first = await provider.create(live.spec)
    second = await provider.create(live._make_spec(live.project_id, SnapshotId("live-snapshot-2")))
    assert first.sandbox_id != second.sandbox_id

    await provider.terminate(first)
    result = await provider.execute(
        second,
        ExecutionRequest(
            step_id=StepId("step-survivor"),
            kind=ExecutionKind.PYTHON,
            code="print('survivor')",
            timeout_seconds=20,
        ),
    )
    assert result.status == ExecutionStatus.SUCCEEDED
    await provider.terminate(second)


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_attestation_fails_closed_on_wrong_digest(live: LiveSandboxFixture) -> None:
    """伪造 digest 无法创建：docker daemon 拒绝未锁定的镜像引用。"""
    bad_spec = live.spec.model_copy(update={"image_digest": "sha256:" + "f" * 64})
    provider = live.provider()
    with pytest.raises(SandboxLostError):
        await provider.create(bad_spec)


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_analysis_runtime_runs_python_and_sql(live: LiveSandboxFixture) -> None:
    """AnalysisRuntime + 真实 Sandbox：Python 与 SQL Step 都产生正式可发布输出。"""
    T0 = datetime(2026, 1, 1, tzinfo=UTC)
    # /project 只读挂载目录中的项目数据（Snapshot 的数据视图）
    (live.project_dir / "data.csv").write_text("id,name\n1,alpha\n2,beta\n", encoding="utf-8")
    runtime_dir = live.root / "runtime"
    runtime_dir.mkdir(parents=True)
    factory = RuntimeConnectionFactory(runtime_dir / "runtime.db")
    store = SqliteRuntimeStore(factory)
    workspace = LocalWorkspace(runtime_dir / "projects")
    corpus = ProjectCorpus(store, workspace, id_factory=DeterministicIdFactory(), clock=lambda: T0)
    project = corpus.create_project("live-analysis")

    source = runtime_dir / "data.csv"
    source.write_text("id,name\n1,alpha\n2,beta\n", encoding="utf-8")
    corpus.import_file(project.id, source)
    snapshot = corpus.create_snapshot(project.id)

    task = Task(id=live.task_id, project_id=project.id, created_at=T0, updated_at=T0)
    run = Run(
        id=live.run_id,
        task_id=task.id,
        project_id=project.id,
        project_snapshot_id=SnapshotId(snapshot.id),
        created_at=T0,
        updated_at=T0,
    )
    with store.unit_of_work() as uow:
        uow.repo.add_task(task)
        uow.repo.add_run(run)
    workspace.create_task(project.id, task.id)

    journal = SqlitePublicationJournal(factory)
    bridge = WorkspaceBridge(workspace, journal, clock=lambda: T0)
    spec = live._make_spec(project.id, SnapshotId(snapshot.id))
    provider = live.provider()
    lease = await provider.create(spec)
    runtime = AnalysisRuntime(
        store,
        corpus,
        workspace,
        provider,
        lease,
        bridge=bridge,
        id_factory=DeterministicIdFactory(),
        clock=lambda: T0,
    )

    python_summary = await runtime.execute_python(
        code="print('runtime python ok')",
        expected_outputs=(OutputSpec(name="report.txt", kind=PublicationKind.ARTIFACT),),
        timeout_seconds=30,
    )
    assert python_summary.status == ExecutionStatus.SUCCEEDED
    assert "runtime python ok" in python_summary.stdout
    assert python_summary.outputs and python_summary.outputs[0].available is True

    sql_summary = await runtime.execute_sql(
        query='SELECT count(*) AS n FROM "data"', timeout_seconds=30
    )
    assert sql_summary.status == ExecutionStatus.SUCCEEDED
    assert "2" in sql_summary.stdout
    await provider.terminate(lease)


@REQUIRES_LIVE
@pytest.mark.asyncio
async def test_live_durable_executor_rebuilds_checkpointed_sandbox(
    live: LiveSandboxFixture,
) -> None:
    """真实耐久编排恢复同一 Run，并在 Sandbox 丢失后按原 Snapshot 重建。"""
    T0 = datetime(2026, 1, 1, tzinfo=UTC)
    runtime_dir = live.root / "orchestration-runtime"
    runtime_dir.mkdir(parents=True)
    factory = RuntimeConnectionFactory(runtime_dir / "runtime.db")
    store = SqliteRuntimeStore(factory)
    workspace = FakeWorkspace(runtime_dir / "workspace")

    project = Project(id=live.project_id, name="live-orchestration", created_at=T0)
    snapshot = ProjectSnapshot(id=live.snapshot_id, project_id=project.id, created_at=T0)
    task = Task(id=live.task_id, project_id=project.id, created_at=T0, updated_at=T0)
    run = Run(
        id=live.run_id,
        task_id=task.id,
        project_id=project.id,
        project_snapshot_id=snapshot.id,
        created_at=T0,
        updated_at=T0,
    )
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_snapshot(snapshot)
        uow.repo.add_task(task)
        uow.repo.add_run(run)
    workspace.create_task(project.id, task.id)

    provider = live.provider()
    calls: list[tuple[bool, RecoveryDecision, SnapshotId]] = []

    async def handler(context: RunExecutionContext) -> RunOutcome:
        """把一次真实 Sandbox Step 与可恢复 checkpoint 绑定。"""
        assert context.sandbox_lease is not None
        calls.append(
            (context.recovered, context.recovery_decision, context.run.project_snapshot_id)
        )
        if len(calls) == 1:
            result = await provider.execute(
                context.sandbox_lease,
                ExecutionRequest(
                    step_id=StepId("step-before-wait"),
                    kind=ExecutionKind.PYTHON,
                    code="print('durable step committed')",
                    timeout_seconds=20,
                ),
            )
            assert result.status == ExecutionStatus.SUCCEEDED
            checkpoint = CheckpointMetadata(
                id="checkpoint-live-recovery",
                run_id=context.run.id,
                sequence=1,
                checkpoint_ref="checkpoint:live-recovery",
                content_hash=ContentHash("sha256:" + "a" * 64),
                project_snapshot_id=context.run.project_snapshot_id,
                sandbox_id=context.sandbox_lease.sandbox_id,
                sandbox_image_digest=context.sandbox_lease.image_digest,
                run_lease_epoch=context.lease_epoch,
                phase=context.run.phase,
                created_at=context.now,
            )
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.USER_INPUT,
                checkpoint=checkpoint,
            )

        assert context.recovered
        assert context.recovery_decision == RecoveryDecision.REBUILD_SANDBOX
        result = await provider.execute(
            context.sandbox_lease,
            ExecutionRequest(
                step_id=StepId("step-after-rebuild"),
                kind=ExecutionKind.PYTHON,
                code="print('durable recovery ok')",
                timeout_seconds=20,
            ),
        )
        assert result.status == ExecutionStatus.SUCCEEDED
        assert "durable recovery ok" in result.stdout
        return RunOutcome(decision=ExecutionDecision.SUCCEEDED)

    def spec_factory(current_run: Run):
        """恢复只能依据 Run 的固定 ID/Snapshot 生成新的 Sandbox 规格。"""
        return live._make_spec(current_run.project_id, current_run.project_snapshot_id)

    first_executor = LocalDurableExecutor(
        store,
        handler,
        owner="live-worker-a",
        clock=lambda: T0,
        backoff_base=0,
        sandbox=provider,
        sandbox_spec_factory=spec_factory,
        workspace=workspace,
    )
    first = await first_executor.run_once()
    assert first is not None and first.status.value == "WAITING"
    with store.unit_of_work() as uow:
        checkpoint = uow.repo.latest_checkpoint(run.id)
    assert checkpoint is not None
    assert checkpoint.project_snapshot_id == snapshot.id

    # WAITING 的 Sandbox 已由 Executor 终止；新的 worker 必须经历 connect 失败并重建。
    with store.unit_of_work() as uow:
        stored = uow.repo.get_run(run.id).value
    assert stored.status.value == "WAITING"
    RunService(store, clock=lambda: T0).resume(run.id)
    second_executor = LocalDurableExecutor(
        store,
        handler,
        owner="live-worker-b",
        clock=lambda: T0,
        backoff_base=0,
        sandbox=provider,
        sandbox_spec_factory=spec_factory,
        workspace=workspace,
    )
    second = await second_executor.run_once()
    with store.unit_of_work() as uow:
        events = uow.repo.list_events("run", str(run.id))
    assert second is not None and second.status.value == "SUCCEEDED", (
        f"second={second!r}; calls={calls!r}; events={events!r}"
    )
    assert calls == [
        (False, RecoveryDecision.START_FROM_BEGINNING, snapshot.id),
        (True, RecoveryDecision.REBUILD_SANDBOX, snapshot.id),
    ]
