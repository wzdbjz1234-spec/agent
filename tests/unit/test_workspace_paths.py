"""Workspace 路径、命名与导入安全的纯边界测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dataharness.domain import ProjectId, StepId, TaskId
from dataharness.providers.workspace import LocalWorkspace, normalize_filename
from dataharness.workspace import UnsafePathError


@pytest.mark.parametrize("name", ["..", "../escape.csv", "C:\\host.txt", "/host.txt", "a/b"])
def test_normalize_filename_rejects_traversal_and_host_paths(name: str) -> None:
    with pytest.raises(UnsafePathError):
        normalize_filename(name)


def test_task_staging_is_derived_from_current_scope(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path / "projects")
    path = workspace.staging_path(
        ProjectId("project-1"), TaskId("task-1"), StepId("step-1"), "result.csv"
    )
    assert path.is_relative_to((tmp_path / "projects" / "project-1").resolve())
    assert "task-1" in path.parts
    with pytest.raises(UnsafePathError):
        workspace.staging_path(
            ProjectId("project-1"), TaskId("task-1"), StepId("step-1"), "../result.csv"
        )


def test_import_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "link.txt"
    os.symlink(target, link)
    workspace = LocalWorkspace(tmp_path / "projects")
    with pytest.raises(UnsafePathError, match="符号链接"):
        workspace.inspect_import(link)


def test_import_rejects_executable_extension(tmp_path: Path) -> None:
    executable = tmp_path / "payload.exe"
    executable.write_bytes(b"MZ synthetic fixture")
    workspace = LocalWorkspace(tmp_path / "projects")
    with pytest.raises(UnsafePathError, match="可执行"):
        workspace.inspect_import(executable)


def test_normalize_filename_preserves_safe_unicode_and_rejects_device_names() -> None:
    assert normalize_filename("研究 数据.csv") == "研究 数据.csv"
    with pytest.raises(UnsafePathError, match="设备名"):
        normalize_filename("CON.txt")


def test_workspace_root_cannot_be_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    link = tmp_path / "linked-root"
    os.symlink(target, link, target_is_directory=True)
    with pytest.raises(UnsafePathError, match="根目录"):
        LocalWorkspace(link)
