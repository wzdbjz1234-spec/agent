"""AnalysisRuntime 与 ProjectCorpus/Workspace/SQLite/Fake Sandbox 的组合验收。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dataharness.analysis import (
    AnalysisCircuitOpenError,
    AnalysisContextError,
    AnalysisRuntime,
    InputReference,
    OutputSpec,
)
from dataharness.domain import (
    ArtifactId,
    EvidenceKind,
    EvidenceRef,
    FileId,
    FileVersionId,
    LineageId,
    Project,
    ProjectFileVersion,
    ProjectSnapshot,
    Run,
    RunId,
    SnapshotId,
    Task,
    TaskId,
    compute_content_hash,
)
from dataharness.idgen import DeterministicIdFactory
from dataharness.projects import ProjectCorpus
from dataharness.providers.sandbox import FakeExecutionPlan, FakeSandboxProvider
from dataharness.providers.workspace import FakeWorkspace
from dataharness.sandbox import SandboxMount, SandboxResources, SandboxSpec, SandboxTimeoutError
from dataharness.storage import (
    RuntimeConnectionFactory,
    SqlitePublicationJournal,
    SqliteRuntimeStore,
)
from dataharness.workspace import PublicationKind, WorkspaceBridge

T0 = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "c" * 64


@dataclass
class RuntimeFixture:
    """带类型的组合 fixture，避免测试把领域对象退化成 object。"""

    store: SqliteRuntimeStore
    corpus: ProjectCorpus
    workspace: FakeWorkspace
    bridge: WorkspaceBridge
    provider: FakeSandboxProvider
    spec: SandboxSpec
    project: Project
    version: ProjectFileVersion
    snapshot: ProjectSnapshot
    task: Task
    run: Run


@pytest.fixture
def runtime_system(tmp_path: Path) -> RuntimeFixture:
    """构造固定 Snapshot 的完整本地事实源与 fake Sandbox。"""
    factory = RuntimeConnectionFactory(tmp_path / "runtime.db")
    store = SqliteRuntimeStore(factory)
    workspace = FakeWorkspace(tmp_path / "projects")
    corpus = ProjectCorpus(store, workspace, id_factory=DeterministicIdFactory(), clock=lambda: T0)
    project = corpus.create_project("analysis")
    source = tmp_path / "input.txt"
    source.write_text("alpha evidence for analysis", encoding="utf-8")
    version = corpus.import_file(project.id, source)
    unsupported = tmp_path / "unsupported.png"
    unsupported.write_bytes(b"\x89PNG\r\n\x1a\n")
    corpus.import_file(project.id, unsupported)
    snapshot = corpus.create_snapshot(project.id)
    task = Task(id=TaskId("task-1"), project_id=project.id, created_at=T0, updated_at=T0)
    run = Run(
        id=RunId("run-1"),
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
    provider = FakeSandboxProvider()
    spec = SandboxSpec(
        project_id=project.id,
        task_id=task.id,
        run_id=run.id,
        project_snapshot_id=snapshot.id,
        image_digest=DIGEST,
        mounts=(
            SandboxMount(source_ref=f"snapshot:{snapshot.id}", target="/project", read_only=True),
            SandboxMount(
                source_ref=f"task:{task.id}:working", target="/task/working", read_only=False
            ),
            SandboxMount(
                source_ref=f"task:{task.id}:staging", target="/task/staging", read_only=False
            ),
        ),
        resources=SandboxResources(
            memory_mb=256, disk_mb=512, max_output_bytes=1024, step_timeout_seconds=30
        ),
    )
    return RuntimeFixture(
        store, corpus, workspace, bridge, provider, spec, project, version, snapshot, task, run
    )


@pytest.mark.asyncio
async def test_execute_python_publishes_artifact_registers_lineage_and_is_idempotent(
    runtime_system: RuntimeFixture,
) -> None:
    """Python 代码只进入 fake Sandbox；发布结果有稳定资源、hash 和输入血缘。"""
    provider = runtime_system.provider
    spec = runtime_system.spec
    lease = await provider.create(spec)
    runtime = AnalysisRuntime(
        runtime_system.store,
        runtime_system.corpus,
        runtime_system.workspace,
        provider,
        lease,
        bridge=runtime_system.bridge,
        id_factory=DeterministicIdFactory("runtime"),
        clock=lambda: T0,
    )
    version = runtime_system.version
    assert version.content_hash is not None
    reference = InputReference(
        file_version_id=version.id,
        file_id=version.file_id,
        content_hash=version.content_hash,
    )
    request_code = "print('generated code')"
    provider.plan("step_000001", FakeExecutionPlan(stdout="result rows"))

    first = await runtime.execute_python(
        request_code,
        inputs=(reference,),
        expected_outputs=(OutputSpec(name="report.txt", kind=PublicationKind.ARTIFACT),),
        timeout_seconds=10,
    )
    second = await runtime.execute_python(
        request_code,
        inputs=(reference,),
        expected_outputs=(OutputSpec(name="report.txt", kind=PublicationKind.ARTIFACT),),
        timeout_seconds=10,
    )
    recovered_runtime = AnalysisRuntime(
        runtime_system.store,
        runtime_system.corpus,
        runtime_system.workspace,
        provider,
        lease,
        bridge=runtime_system.bridge,
        id_factory=DeterministicIdFactory("recovered"),
        clock=lambda: T0,
    )
    recovered = await recovered_runtime.execute_python(
        request_code,
        inputs=(reference,),
        expected_outputs=(OutputSpec(name="report.txt", kind=PublicationKind.ARTIFACT),),
        timeout_seconds=10,
    )

    assert first == second
    assert recovered == first
    assert provider.received_code == [request_code]
    assert first.outputs[0].available is True
    assert first.outputs[0].content_hash == compute_content_hash(b"result rows")
    with runtime_system.store.unit_of_work() as uow:
        artifact = uow.repo.get_artifact(ArtifactId(first.outputs[0].resource_id))
        lineage = uow.repo.get_lineage(LineageId("lineage_000003"))
        code_lineage = uow.repo.get_lineage(LineageId("lineage_000004"))
    assert artifact.content_hash == first.outputs[0].content_hash
    assert lineage.source.resource_id == str(version.id)
    assert lineage.target.resource_id == first.outputs[0].resource_id
    assert code_lineage.source.resource_id == str(first.step_id)
    assert code_lineage.source.content_hash == first.code_hash


@pytest.mark.asyncio
async def test_sql_input_context_is_snapshot_bound_and_output_inspection_is_bounded(
    runtime_system: RuntimeFixture,
) -> None:
    """SQL 入口不接受跨 Snapshot 输入，输出检查只读取当前 Step staging。"""
    provider = runtime_system.provider
    lease = await provider.create(runtime_system.spec)
    runtime = AnalysisRuntime(
        runtime_system.store,
        runtime_system.corpus,
        runtime_system.workspace,
        provider,
        lease,
        id_factory=DeterministicIdFactory("runtime"),
        clock=lambda: T0,
    )
    with pytest.raises(AnalysisContextError):
        await runtime.execute_sql(
            "SELECT 1",
            inputs=(
                InputReference(
                    file_version_id=FileVersionId("foreign-version"),
                    file_id=FileId("foreign-file"),
                    content_hash=compute_content_hash(b"foreign"),
                ),
            ),
            timeout_seconds=10,
        )
    provider.plan("step_000001", FakeExecutionPlan(stdout="1,2\n"))
    summary = await runtime.execute_sql(
        "SELECT 1",
        expected_outputs=(OutputSpec(name="rows.csv", kind=PublicationKind.DATASET),),
        timeout_seconds=10,
    )
    inspection = runtime.inspect_output(str(summary.step_id), "rows.csv")
    assert inspection.excerpt == "1,2\n"
    assert inspection.content_hash == summary.outputs[0].content_hash


@pytest.mark.asyncio
async def test_full_project_reports_unsupported_gap_and_finding_stays_draft(
    runtime_system: RuntimeFixture,
) -> None:
    """FULL_PROJECT 只枚举 Snapshot，覆盖缺口显式保留；Finding 仅提交 DRAFT。"""
    corpus = runtime_system.corpus
    provider = runtime_system.provider
    lease = await provider.create(runtime_system.spec)
    runtime = AnalysisRuntime(
        runtime_system.store,
        corpus,
        runtime_system.workspace,
        provider,
        lease,
        clock=lambda: T0,
    )
    views = runtime.list_project_files()
    assert {item.file_version_id for item in views} == {
        runtime_system.snapshot.entries[0].file_version_id,
        runtime_system.snapshot.entries[1].file_version_id,
    }
    hits = runtime.search_project("alpha")
    assert len(hits) == 1
    inspected = runtime.inspect_project_file(str(runtime_system.version.id), max_chars=5)
    assert inspected.excerpt == "alpha"
    coverage = runtime.get_project_coverage()
    assert coverage.total == 2
    full = await runtime.execute_full_project("print('batch')", batch_size=1, timeout_seconds=10)
    assert full.total_files == 2
    assert full.uncovered_files == 1
    assert len(full.batches) == 1
    assert runtime_system.version.content_hash is not None
    finding = runtime.submit_finding(
        "合成结论",
        (
            EvidenceRef(
                kind=EvidenceKind.FILE,
                target_id=str(runtime_system.version.id),
                content_hash=runtime_system.version.content_hash,
            ),
        ),
    )
    assert finding.status == "DRAFT"


@pytest.mark.asyncio
async def test_repeated_sandbox_timeouts_open_circuit_breaker(
    runtime_system: RuntimeFixture,
) -> None:
    """同一规范请求连续失败达到上限后必须 fail closed。"""
    provider = runtime_system.provider
    lease = await provider.create(runtime_system.spec)
    runtime = AnalysisRuntime(
        runtime_system.store,
        runtime_system.corpus,
        runtime_system.workspace,
        provider,
        lease,
        id_factory=DeterministicIdFactory("circuit"),
        clock=lambda: T0,
        max_consecutive_failures=3,
    )
    for step_number in range(1, 4):
        provider.plan(f"step_{step_number:06d}", FakeExecutionPlan(timeout=True))
        with pytest.raises(SandboxTimeoutError):
            await runtime.execute_python("raise TimeoutError()", timeout_seconds=10)
    with pytest.raises(AnalysisCircuitOpenError):
        await runtime.execute_python("raise TimeoutError()", timeout_seconds=10)
    assert len(provider.received_code) == 3
