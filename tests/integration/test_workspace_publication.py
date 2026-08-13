"""SQLite 发布日志与本地文件系统的崩溃对账组合测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dataharness.domain import (
    AnalysisStep,
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
    compute_content_hash,
)
from dataharness.providers.workspace import LocalWorkspace
from dataharness.storage import (
    RuntimeConnectionFactory,
    SqlitePublicationJournal,
    SqliteRuntimeStore,
)
from dataharness.workspace import (
    PublicationError,
    PublicationKind,
    PublicationStatus,
    WorkspaceBridge,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _system(
    tmp_path: Path,
) -> tuple[LocalWorkspace, SqlitePublicationJournal, WorkspaceBridge]:
    factory = RuntimeConnectionFactory(tmp_path / "runtime.db")
    store = SqliteRuntimeStore(factory)
    workspace = LocalWorkspace(tmp_path / "projects")
    project = Project(id=ProjectId("project-1"), name="publish", created_at=T0)
    snapshot = ProjectSnapshot(id=SnapshotId("snapshot-1"), project_id=project.id, created_at=T0)
    task = Task(id=TaskId("task-1"), project_id=project.id, created_at=T0, updated_at=T0)
    run = Run(
        id=RunId("run-1"),
        task_id=task.id,
        project_id=project.id,
        project_snapshot_id=snapshot.id,
        created_at=T0,
        updated_at=T0,
    )
    step = AnalysisStep(id=StepId("step-1"), run_id=run.id, created_at=T0)
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_snapshot(snapshot)
        uow.repo.add_task(task)
        uow.repo.add_run(run)
        uow.repo.add_step(step)
    workspace.create_task(project.id, task.id)
    journal = SqlitePublicationJournal(factory)
    bridge = WorkspaceBridge(workspace, journal, clock=lambda: T0)
    return workspace, journal, bridge


def _stage(
    workspace: LocalWorkspace,
    bridge: WorkspaceBridge,
    *,
    output_name: str,
    resource_id: str,
) -> str:
    data = f"content:{output_name}".encode()
    path = workspace.staging_path(
        ProjectId("project-1"), TaskId("task-1"), StepId("step-1"), output_name
    )
    path.write_bytes(data)
    record = bridge.stage(
        project_id=ProjectId("project-1"),
        task_id=TaskId("task-1"),
        run_id=RunId("run-1"),
        step_id=StepId("step-1"),
        output_name=output_name,
        kind=PublicationKind.ARTIFACT,
        resource_id=resource_id,
        content_hash=compute_content_hash(data),
        byte_size=len(data),
    )
    return record.idempotency_key


def test_reconciler_recovers_before_and_after_atomic_move(tmp_path: Path) -> None:
    workspace, journal, bridge = _system(tmp_path)
    before_move = _stage(workspace, bridge, output_name="before.txt", resource_id="artifact-1")
    assert bridge.available(ProjectId("project-1")) == ()
    assert bridge.reconcile()[0].status == PublicationStatus.AVAILABLE
    assert bridge.publish(before_move).name == "before.txt"
    assert len(bridge.available(ProjectId("project-1"))) == 1

    after_move = _stage(workspace, bridge, output_name="after.txt", resource_id="artifact-2")
    record = journal.get(after_move)
    assert record is not None
    workspace.publish_staged(record)  # 模拟文件已移动、数据库尚未 AVAILABLE 时 Host 崩溃。
    reconciled = bridge.reconcile()
    assert reconciled[0].status == PublicationStatus.AVAILABLE


def test_missing_staging_converges_to_explicit_corrupt(tmp_path: Path) -> None:
    workspace, _, bridge = _system(tmp_path)
    key = _stage(workspace, bridge, output_name="missing.txt", resource_id="artifact-1")
    workspace.staging_path(
        ProjectId("project-1"), TaskId("task-1"), StepId("step-1"), "missing.txt"
    ).unlink()
    record = bridge.reconcile()[0]
    assert record.status == PublicationStatus.CORRUPT
    with pytest.raises(PublicationError):
        bridge.publish(key)


def test_idempotency_key_rejects_changed_request(tmp_path: Path) -> None:
    workspace, _, bridge = _system(tmp_path)
    _stage(workspace, bridge, output_name="same.txt", resource_id="artifact-1")
    data = b"changed"
    with pytest.raises(PublicationError, match="不同发布请求"):
        bridge.stage(
            project_id=ProjectId("project-1"),
            task_id=TaskId("task-1"),
            run_id=RunId("run-1"),
            step_id=StepId("step-1"),
            output_name="same.txt",
            kind=PublicationKind.ARTIFACT,
            resource_id="artifact-1",
            content_hash=ContentHash("f" * 64),
            byte_size=len(data),
        )
