"""RuntimeRepository 的可观察行为契约。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dataharness.domain import (
    AnalysisStep,
    Artifact,
    ArtifactId,
    ContentHash,
    CoverageItem,
    CoverageItemStatus,
    CoverageReportId,
    Dataset,
    DatasetId,
    EvidenceKind,
    EvidenceRef,
    FileId,
    FileVersionId,
    Finding,
    FindingCandidate,
    FindingId,
    Lineage,
    LineageId,
    Project,
    ProjectCoverageReport,
    ProjectFile,
    ProjectFileVersion,
    ProjectId,
    ProjectSnapshot,
    ResourceKind,
    ResourceRef,
    Run,
    RunId,
    Session,
    SessionId,
    SnapshotEntry,
    SnapshotId,
    StepId,
    Task,
    TaskId,
)
from dataharness.storage import (
    CheckpointMetadata,
    ConcurrencyConflictError,
    IdempotencyConflictError,
    IdempotencyRecord,
    InvalidMetadataError,
    RuntimeConnectionFactory,
    SqliteRuntimeStore,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
HASH = ContentHash("a" * 64)


def _store(path: Path) -> SqliteRuntimeStore:
    return SqliteRuntimeStore(RuntimeConnectionFactory(path / "runtime.db"))


def _seed(store: SqliteRuntimeStore) -> tuple[ProjectSnapshot, Task, Run]:
    """建立最小且外键完整的 Project -> Snapshot -> Task -> Run 图。"""
    project = Project(id=ProjectId("project-1"), name="synthetic", created_at=T0)
    file = ProjectFile(id=FileId("file-1"), project_id=project.id, name="input.csv", created_at=T0)
    version = ProjectFileVersion(
        id=FileVersionId("version-1"),
        file_id=file.id,
        project_id=project.id,
        version_number=1,
        created_at=T0,
    ).mark_ready(HASH, 12, "text/csv", T0)
    snapshot = ProjectSnapshot(
        id=SnapshotId("snapshot-1"),
        project_id=project.id,
        created_at=T0,
        index_version="fts-v1",
        entries=(
            SnapshotEntry(
                file_version_id=version.id,
                file_id=file.id,
                version_number=1,
                status=version.status,
                content_hash=HASH,
            ),
        ),
    )
    session = Session(id=SessionId("session-1"), label="test", created_at=T0)
    task = Task(
        id=TaskId("task-1"),
        project_id=project.id,
        session_id=session.id,
        created_at=T0,
        updated_at=T0,
    )
    run = Run(
        id=RunId("run-1"),
        task_id=task.id,
        project_id=project.id,
        project_snapshot_id=snapshot.id,
        created_at=T0,
        updated_at=T0,
    )
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_file(file)
        importing = version.model_copy(
            update={
                "status": "IMPORTING",
                "content_hash": None,
                "byte_size": None,
                "media_type": None,
                "finalized_at": None,
            }
        )
        uow.repo.add_file_version(importing)
        uow.repo.finalize_file_version(version, 0)
        uow.repo.add_snapshot(snapshot)
        uow.repo.add_session(session)
        uow.repo.add_task(task)
        uow.repo.add_run(run)
    return snapshot, task, run


def test_repository_round_trips_all_phase_02_domain_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot, task, run = _seed(store)
    step = AnalysisStep(id=StepId("step-1"), run_id=run.id, created_at=T0)
    dataset = Dataset(
        id=DatasetId("dataset-1"),
        project_id=run.project_id,
        task_id=task.id,
        run_id=run.id,
        name="clean.csv",
        content_hash=HASH,
        created_at=T0,
    )
    artifact = Artifact(
        id=ArtifactId("artifact-1"),
        project_id=run.project_id,
        task_id=task.id,
        run_id=run.id,
        name="report.html",
        content_hash=HASH,
        created_at=T0,
    )
    coverage = ProjectCoverageReport(
        id=CoverageReportId("coverage-1"),
        snapshot_id=snapshot.id,
        created_at=T0,
        items=(
            CoverageItem(
                file_version_id=FileVersionId("version-1"),
                file_id=FileId("file-1"),
                status=CoverageItemStatus.PROCESSED,
            ),
        ),
    )
    finding = Finding(
        id=FindingId("finding-1"),
        candidate=FindingCandidate(
            task_id=task.id,
            run_id=run.id,
            project_snapshot_id=snapshot.id,
            summary="合成结论",
            evidence=(
                EvidenceRef(
                    kind=EvidenceKind.DATASET,
                    target_id=str(dataset.id),
                    content_hash=HASH,
                ),
            ),
            created_at=T0,
        ),
    )
    lineage = Lineage(
        id=LineageId("lineage-1"),
        run_id=run.id,
        source=ResourceRef(
            kind=ResourceKind.FILE_VERSION, resource_id="version-1", content_hash=HASH
        ),
        target=ResourceRef(
            kind=ResourceKind.DATASET, resource_id=str(dataset.id), content_hash=HASH
        ),
        created_at=T0,
    )
    checkpoint = CheckpointMetadata(
        id="checkpoint-1",
        run_id=run.id,
        sequence=1,
        checkpoint_ref="pydantic-ai://run-1/1",
        content_hash=HASH,
        created_at=T0,
    )
    with store.unit_of_work() as uow:
        uow.repo.add_step(step)
        uow.repo.add_dataset(dataset)
        uow.repo.add_artifact(artifact)
        uow.repo.add_coverage_report(coverage)
        uow.repo.add_finding(finding)
        uow.repo.add_lineage(lineage)
        uow.repo.add_checkpoint(checkpoint)

    with store.unit_of_work() as uow:
        assert uow.repo.get_snapshot(snapshot.id) == snapshot
        assert uow.repo.get_step(step.id).value == step
        assert uow.repo.get_dataset(dataset.id) == dataset
        assert uow.repo.get_artifact(artifact.id) == artifact
        assert uow.repo.get_coverage_report(coverage.id) == coverage
        assert uow.repo.get_finding(finding.id).value == finding
        assert uow.repo.get_lineage(lineage.id) == lineage
        assert uow.repo.latest_checkpoint(run.id) == checkpoint


def test_cas_transition_and_event_commit_or_rollback_together(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, task, _ = _seed(store)
    started = task.start(T0)
    with pytest.raises(RuntimeError, match="injected"), store.unit_of_work() as uow:
        uow.repo.save_task(started, 0, "TASK_STARTED")
        raise RuntimeError("injected")

    with store.unit_of_work() as uow:
        assert uow.repo.get_task(task.id).value == task
        assert [event.event_type for event in uow.repo.list_events("task", str(task.id))] == [
            "TASK_CREATED"
        ]

    with store.unit_of_work() as uow:
        stored = uow.repo.save_task(started, 0, "TASK_STARTED")
        assert stored.version == 1
    with store.unit_of_work() as uow, pytest.raises(ConcurrencyConflictError):
        uow.repo.save_task(started.complete(T0), 0, "TASK_COMPLETED")


def test_terminal_state_cannot_be_reopened_by_direct_model_construction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, task, _ = _seed(store)
    terminal = task.start(T0).complete(T0)
    with store.unit_of_work() as uow:
        active = uow.repo.save_task(task.start(T0), 0, "TASK_STARTED")
        uow.repo.save_task(terminal, active.version, "TASK_COMPLETED")
    reopened = task.model_copy(update={"status": "ACTIVE", "completed_at": None})
    with store.unit_of_work() as uow, pytest.raises(ConcurrencyConflictError):
        uow.repo.save_task(reopened, 2, "TASK_REOPENED")


def test_idempotency_key_replays_same_request_and_rejects_different_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = IdempotencyRecord(
        scope="publish",
        key="run-1/step-1/output",
        request_hash=HASH,
        result_ref=None,
        created_at=T0,
    )
    with store.unit_of_work() as uow:
        assert uow.repo.reserve_idempotency(record) == record
        assert uow.repo.reserve_idempotency(record) == record
        done = uow.repo.complete_idempotency(record.scope, record.key, HASH, "artifact-1")
        assert done.result_ref == "artifact-1"
    conflict = record.model_copy(update={"request_hash": ContentHash("b" * 64)})
    with store.unit_of_work() as uow, pytest.raises(IdempotencyConflictError):
        uow.repo.reserve_idempotency(conflict)


def test_event_interface_rejects_raw_model_or_oversized_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.unit_of_work() as uow, pytest.raises(InvalidMetadataError):
        uow.repo.append_event("run", "run-1", "BAD", T0, {"model_payload": "raw"})
    with store.unit_of_work() as uow, pytest.raises(InvalidMetadataError):
        uow.repo.append_event("run", "run-1", "BAD", T0, {"detail": "x" * 20_000})
