"""Local/Fake Workspace 共享行为契约。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataharness.domain import FileId, FileVersionId, ProjectId, TaskId, compute_content_hash
from dataharness.providers.workspace import FakeWorkspace, LocalWorkspace
from dataharness.workspace import ResourceIntegrityError, VirtualWorkspace


@pytest.fixture(params=[LocalWorkspace, FakeWorkspace], ids=["local", "fake"])
def workspace(request: pytest.FixtureRequest, tmp_path: Path) -> VirtualWorkspace:
    return request.param(tmp_path / request.param.__name__)


def test_source_versions_are_immutable_and_manifest_is_idempotent(
    workspace: VirtualWorkspace, tmp_path: Path
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("first version", encoding="utf-8")
    project_id = ProjectId("project-1")
    file_id = FileId("file-1")
    version_id = FileVersionId("version-1")
    content_hash = compute_content_hash(source.read_bytes())
    resource = workspace.import_source(
        project_id, file_id, version_id, source, "input.txt", content_hash
    )
    assert resource.content_hash == content_hash
    assert (
        workspace.source_path(project_id, file_id, version_id, "input.txt").read_text("utf-8")
        == "first version"
    )

    with pytest.raises(FileExistsError):
        workspace.import_source(project_id, file_id, version_id, source, "input.txt", content_hash)
    first = workspace.write_manifest(project_id, "snapshot-1.json", b"{}")
    assert workspace.write_manifest(project_id, "snapshot-1.json", b"{}") == first
    with pytest.raises(ResourceIntegrityError):
        workspace.write_manifest(project_id, "snapshot-1.json", b'{"changed":true}')


def test_task_working_and_run_manifest_are_scoped_and_atomic(
    workspace: VirtualWorkspace,
) -> None:
    project_id = ProjectId("project-1")
    task_id = TaskId("task-1")
    working = workspace.write_working(project_id, task_id, "analysis.sql", b"SELECT 1")
    assert working.namespace == "working"
    first = workspace.write_task_state(project_id, task_id, "RUN.json", b'{"snapshot":"s1"}')
    assert (
        workspace.write_task_state(project_id, task_id, "RUN.json", b'{"snapshot":"s1"}') == first
    )
    with pytest.raises(ResourceIntegrityError, match="不可变"):
        workspace.write_task_state(project_id, task_id, "RUN.json", b'{"snapshot":"s2"}')
