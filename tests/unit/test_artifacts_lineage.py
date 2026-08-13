"""正式资源与血缘领域对象测试。"""

from __future__ import annotations

from datetime import datetime

from dataharness.domain import (
    Artifact,
    Dataset,
    Lineage,
    ResourceKind,
    ResourceRef,
    compute_content_hash,
)
from dataharness.domain.ids import (
    ArtifactId,
    ContentHash,
    DatasetId,
    LineageId,
    ProjectId,
    RunId,
)


def test_dataset_and_artifact_carry_stable_hash(t0: datetime) -> None:
    digest = compute_content_hash(b"data")
    dataset = Dataset(
        id=DatasetId("d1"),
        project_id=ProjectId("p1"),
        name="d",
        content_hash=digest,
        created_at=t0,
    )
    artifact = Artifact(
        id=ArtifactId("a1"),
        project_id=ProjectId("p1"),
        name="a",
        content_hash=digest,
        created_at=t0,
    )
    assert dataset.content_hash == digest
    assert artifact.content_hash == digest


def test_lineage_records_source_to_target(t0: datetime) -> None:
    lineage = Lineage(
        id=LineageId("l1"),
        run_id=RunId("r1"),
        source=ResourceRef(
            kind=ResourceKind.FILE_VERSION,
            resource_id="fv1",
            content_hash=ContentHash("h1"),
        ),
        target=ResourceRef(kind=ResourceKind.DATASET, resource_id="d1"),
        created_at=t0,
    )
    assert lineage.source.kind is ResourceKind.FILE_VERSION
    assert lineage.target.resource_id == "d1"
