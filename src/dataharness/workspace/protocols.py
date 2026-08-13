"""Workspace 小型协议；调用方不依赖本地路径或 SQLite 实现。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dataharness.domain import ContentHash, FileId, FileVersionId, ProjectId, StepId, TaskId

from .models import PublicationRecord, PublicationStatus, WorkspaceResource


class VirtualWorkspace(Protocol):
    """受控项目/任务文件命名空间。"""

    def create_project(self, project_id: ProjectId) -> None: ...

    def create_task(self, project_id: ProjectId, task_id: TaskId) -> None: ...

    def inspect_import(self, source: Path) -> tuple[int, ContentHash]: ...

    def import_source(
        self,
        project_id: ProjectId,
        file_id: FileId,
        version_id: FileVersionId,
        source: Path,
        normalized_name: str,
        expected_hash: ContentHash,
    ) -> WorkspaceResource: ...

    def source_path(
        self, project_id: ProjectId, file_id: FileId, version_id: FileVersionId, name: str
    ) -> Path: ...

    def extracted_path(self, project_id: ProjectId, version_id: FileVersionId) -> Path: ...

    def index_path(self, project_id: ProjectId) -> Path: ...

    def write_manifest(
        self, project_id: ProjectId, name: str, data: bytes
    ) -> WorkspaceResource: ...

    def write_working(
        self, project_id: ProjectId, task_id: TaskId, name: str, data: bytes
    ) -> WorkspaceResource: ...

    def write_task_state(
        self, project_id: ProjectId, task_id: TaskId, name: str, data: bytes
    ) -> WorkspaceResource: ...

    def staging_path(
        self, project_id: ProjectId, task_id: TaskId, step_id: StepId, output_name: str
    ) -> Path: ...

    def published_resource(self, record: PublicationRecord) -> WorkspaceResource: ...

    def publish_staged(self, record: PublicationRecord) -> WorkspaceResource: ...

    def resource_exists(self, record: PublicationRecord) -> bool: ...


class PublicationJournal(Protocol):
    """发布元数据事实源协议。"""

    def stage(self, record: PublicationRecord) -> PublicationRecord: ...

    def get(self, idempotency_key: str) -> PublicationRecord | None: ...

    def set_status(
        self, idempotency_key: str, status: PublicationStatus, detail: str | None = None
    ) -> PublicationRecord: ...

    def pending(self) -> tuple[PublicationRecord, ...]: ...

    def available(self, project_id: ProjectId) -> tuple[PublicationRecord, ...]: ...
