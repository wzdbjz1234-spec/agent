"""ProjectCorpus 的不可变版本、Snapshot 和检索契约。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataharness.domain import FileVersionStatus
from dataharness.idgen import DeterministicIdFactory
from dataharness.projects import ProjectCorpus
from dataharness.providers.workspace import FakeWorkspace, LocalWorkspace
from dataharness.storage import RuntimeConnectionFactory, SqliteRuntimeStore


@pytest.fixture(params=[LocalWorkspace, FakeWorkspace], ids=["local", "fake"])
def corpus(request: pytest.FixtureRequest, tmp_path: Path) -> ProjectCorpus:
    store = SqliteRuntimeStore(
        RuntimeConnectionFactory(tmp_path / request.param.__name__ / "runtime.db")
    )
    workspace = request.param(tmp_path / request.param.__name__ / "projects")
    return ProjectCorpus(store, workspace, id_factory=DeterministicIdFactory())


def test_version_snapshot_and_relevant_source_contract(
    corpus: ProjectCorpus, tmp_path: Path
) -> None:
    project = corpus.create_project("research")
    source = tmp_path / "notes.md"
    source.write_text("第一版包含 alpha evidence", encoding="utf-8")
    first = corpus.import_file(project.id, source)
    frozen = corpus.create_snapshot(project.id)

    source.write_text("第二版包含 beta evidence", encoding="utf-8")
    second = corpus.import_file(project.id, source)
    latest = corpus.create_snapshot(project.id)

    assert first.status == second.status == FileVersionStatus.READY
    assert second.version_number == first.version_number + 1
    assert frozen.entries[0].file_version_id == first.id
    assert latest.entries[0].file_version_id == second.id
    assert corpus.search(frozen.id, "alpha")[0].file_version_id == first.id
    assert corpus.search(frozen.id, "beta") == ()
    hit = corpus.search(latest.id, "beta")[0]
    assert hit.file_version_id == second.id
    assert hit.locator == {"paragraph": 1}
    opened = corpus.open_resource(latest.id, second.id)
    assert opened.data.decode("utf-8") == "第二版包含 beta evidence"
    assert opened.content_hash == second.content_hash


def test_archived_project_rejects_new_import(corpus: ProjectCorpus, tmp_path: Path) -> None:
    project = corpus.create_project("archived")
    assert corpus.archive_project(project.id).status == "ARCHIVED"
    source = tmp_path / "later.txt"
    source.write_text("must not import", encoding="utf-8")
    with pytest.raises(ValueError, match="归档"):
        corpus.import_file(project.id, source)


def test_full_project_explicitly_reports_unsupported(corpus: ProjectCorpus, tmp_path: Path) -> None:
    project = corpus.create_project("coverage")
    supported = tmp_path / "good.txt"
    supported.write_text("covered", encoding="utf-8")
    unsupported = tmp_path / "image.png"
    unsupported.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    corpus.import_file(project.id, supported)
    version = corpus.import_file(project.id, unsupported)
    snapshot = corpus.create_snapshot(project.id)
    report = corpus.full_project_coverage(snapshot.id)

    assert version.status == FileVersionStatus.UNSUPPORTED
    assert report.processed == 1
    assert report.unsupported == 1
    assert report.has_uncovered()
