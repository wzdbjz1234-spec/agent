"""Finding 领域对象测试：候选证据要求与 Host Gate 正式状态迁移。"""

from __future__ import annotations

from datetime import datetime

import pytest

from dataharness.domain import (
    EvidenceKind,
    EvidenceRef,
    Finding,
    FindingCandidate,
    FindingStatus,
    IllegalStateTransitionError,
    InvalidEvidenceError,
    compute_content_hash,
)
from dataharness.domain.ids import (
    ContentHash,
    FindingId,
    RunId,
    SnapshotId,
    TaskId,
)


def make_candidate() -> FindingCandidate:
    return FindingCandidate(
        task_id=TaskId("t1"),
        run_id=RunId("r1"),
        project_snapshot_id=SnapshotId("s1"),
        summary="summary",
        evidence=(
            EvidenceRef(
                kind=EvidenceKind.FILE,
                target_id="fv1",
                content_hash=compute_content_hash(b"data"),
                locator="page 2",
            ),
        ),
    )


def test_candidate_requires_at_least_one_evidence() -> None:
    with pytest.raises(InvalidEvidenceError):
        FindingCandidate(
            task_id=TaskId("t1"),
            run_id=RunId("r1"),
            project_snapshot_id=SnapshotId("s1"),
            summary="summary",
            evidence=(),
        )


def test_finding_starts_as_draft(t0: datetime) -> None:
    finding = Finding(id=FindingId("f1"), candidate=make_candidate())
    assert finding.status is FindingStatus.DRAFT


@pytest.mark.parametrize(
    ("method_name", "expected"),
    [
        ("verify", FindingStatus.VERIFIED),
        ("mark_warning", FindingStatus.WARNING),
        ("reject", FindingStatus.REJECTED),
    ],
)
def test_host_gate_transitions_from_draft(
    method_name: str, expected: FindingStatus, t0: datetime
) -> None:
    finding = Finding(id=FindingId("f1"), candidate=make_candidate())
    assert getattr(finding, method_name)(t0).status is expected


def test_finding_terminal_state_cannot_change(t0: datetime) -> None:
    verified = Finding(id=FindingId("f1"), candidate=make_candidate()).verify(t0)
    for method in ("verify", "mark_warning", "reject"):
        with pytest.raises(IllegalStateTransitionError):
            getattr(verified, method)(t0)


def test_evidence_ref_carries_hash_and_locator() -> None:
    ref = EvidenceRef(
        kind=EvidenceKind.DATASET,
        target_id="d1",
        content_hash=ContentHash("h"),
        locator=None,
    )
    assert ref.kind is EvidenceKind.DATASET
    assert ref.content_hash == ContentHash("h")
