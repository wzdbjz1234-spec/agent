"""Project 语料领域对象测试：不可变版本、Snapshot、归档与覆盖报告。"""

from __future__ import annotations

from datetime import datetime

import pytest

from dataharness.domain import (
    CoverageItem,
    CoverageItemStatus,
    FileVersionImmutableError,
    FileVersionStatus,
    Project,
    ProjectCoverageReport,
    ProjectFile,
    ProjectFileVersion,
    ProjectSnapshot,
    ProjectStatus,
    SnapshotEntry,
    compute_content_hash,
)
from dataharness.domain.ids import (
    ContentHash,
    CoverageReportId,
    FileId,
    FileVersionId,
    ProjectId,
    SnapshotId,
)


def make_version(
    status: FileVersionStatus = FileVersionStatus.IMPORTING,
) -> ProjectFileVersion:
    return ProjectFileVersion(
        id=FileVersionId("fv1"),
        file_id=FileId("f1"),
        project_id=ProjectId("p1"),
        version_number=1,
        status=status,
    )


def test_archive_transitions_active_to_archived(t0: datetime) -> None:
    project = Project(id=ProjectId("p1"), name="n", created_at=t0)
    archived = project.archive(t0)
    assert archived.status is ProjectStatus.ARCHIVED
    assert archived.archived_at == t0


def test_archive_is_idempotent(t0: datetime) -> None:
    archived = Project(id=ProjectId("p1"), name="n", created_at=t0).archive(t0)
    assert archived.archive(t0) is archived


def test_mark_ready_binds_hash_size_and_media(t0: datetime) -> None:
    version = make_version()
    digest = compute_content_hash(b"data")
    ready = version.mark_ready(digest, 4, "text/plain", t0)
    assert ready.status is FileVersionStatus.READY
    assert ready.content_hash == digest
    assert ready.byte_size == 4
    assert ready.media_type == "text/plain"
    assert ready.finalized_at == t0


@pytest.mark.parametrize(
    "final_state",
    [FileVersionStatus.READY, FileVersionStatus.FAILED, FileVersionStatus.UNSUPPORTED],
)
def test_finalized_version_is_immutable(final_state: FileVersionStatus) -> None:
    version = make_version(status=final_state)
    with pytest.raises(FileVersionImmutableError):
        version.mark_ready(ContentHash("h"), 1, "text/plain")
    with pytest.raises(FileVersionImmutableError):
        version.mark_failed()
    with pytest.raises(FileVersionImmutableError):
        version.mark_unsupported()


def test_mark_failed_and_unsupported_from_importing() -> None:
    assert make_version().mark_failed().status is FileVersionStatus.FAILED
    assert make_version().mark_unsupported().status is FileVersionStatus.UNSUPPORTED


def test_project_file_holds_logical_file(t0: datetime) -> None:
    project_file = ProjectFile(
        id=FileId("f1"), project_id=ProjectId("p1"), name="a.csv", created_at=t0
    )
    assert project_file.name == "a.csv"
    assert project_file.project_id == ProjectId("p1")


def test_snapshot_ready_entries_filters_non_ready(t0: datetime) -> None:
    snapshot = ProjectSnapshot(
        id=SnapshotId("s1"),
        project_id=ProjectId("p1"),
        created_at=t0,
        entries=(
            SnapshotEntry(
                file_version_id=FileVersionId("v1"),
                file_id=FileId("f1"),
                version_number=1,
                status=FileVersionStatus.READY,
                content_hash=ContentHash("h1"),
            ),
            SnapshotEntry(
                file_version_id=FileVersionId("v2"),
                file_id=FileId("f2"),
                version_number=1,
                status=FileVersionStatus.UNSUPPORTED,
            ),
        ),
    )
    ready = snapshot.ready_entries()
    assert [e.file_version_id for e in ready] == [FileVersionId("v1")]


def test_snapshot_entry_for_lookup(t0: datetime) -> None:
    snapshot = ProjectSnapshot(
        id=SnapshotId("s1"),
        project_id=ProjectId("p1"),
        created_at=t0,
        entries=(
            SnapshotEntry(
                file_version_id=FileVersionId("v1"),
                file_id=FileId("f1"),
                version_number=1,
                status=FileVersionStatus.READY,
            ),
        ),
    )
    assert snapshot.entry_for(FileVersionId("v1")) is not None
    assert snapshot.entry_for(FileVersionId("missing")) is None


def test_coverage_report_counts_and_uncovered(t0: datetime) -> None:
    report = ProjectCoverageReport(
        id=CoverageReportId("c1"),
        snapshot_id=SnapshotId("s1"),
        created_at=t0,
        items=(
            CoverageItem(
                file_version_id=FileVersionId("v1"),
                file_id=FileId("f1"),
                status=CoverageItemStatus.PROCESSED,
            ),
            CoverageItem(
                file_version_id=FileVersionId("v2"),
                file_id=FileId("f2"),
                status=CoverageItemStatus.UNSUPPORTED,
                detail="unsupported format",
            ),
            CoverageItem(
                file_version_id=FileVersionId("v3"),
                file_id=FileId("f3"),
                status=CoverageItemStatus.FAILED,
            ),
        ),
    )
    assert report.total == 3
    assert report.processed == 1
    assert report.unsupported == 1
    assert report.failed == 1
    assert report.skipped == 0
    assert report.has_uncovered()


def test_coverage_report_fully_processed_has_no_uncovered(t0: datetime) -> None:
    report = ProjectCoverageReport(
        id=CoverageReportId("c1"),
        snapshot_id=SnapshotId("s1"),
        created_at=t0,
        items=(
            CoverageItem(
                file_version_id=FileVersionId("v1"),
                file_id=FileId("f1"),
                status=CoverageItemStatus.PROCESSED,
            ),
        ),
    )
    assert not report.has_uncovered()
