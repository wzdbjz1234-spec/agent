"""Project 语料领域对象。

包含 Project、ProjectFile、ProjectFileVersion、ProjectSnapshot、SnapshotEntry、
CoverageItem 与 ProjectCoverageReport。核心不变量：

- Project 是长期容器，ACTIVE 可导入新文件与创建 Task，ARCHIVED 为终态。
- 同一逻辑文件的更新创建新的 ProjectFileVersion，绝不覆盖旧版本。
- ProjectFileVersion 定稿（READY/FAILED/UNSUPPORTED）后不可变。
- ProjectSnapshot 创建后不可变，不提供任何更新操作。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .clock import utcnow
from .enums import CoverageItemStatus, FileVersionStatus, ProjectStatus
from .errors import FileVersionImmutableError
from .ids import (
    ContentHash,
    CoverageReportId,
    DatasetId,
    FileId,
    FileVersionId,
    ProjectId,
    SnapshotId,
)

# 文件版本定稿状态集合：进入这些状态后版本不可变
FILE_VERSION_FINAL_STATES = frozenset(
    {FileVersionStatus.READY, FileVersionStatus.FAILED, FileVersionStatus.UNSUPPORTED}
)


class Project(BaseModel):
    """长期项目容器。

    归档是终态且幂等：已归档项目再次归档返回自身；归档不删除历史 Snapshot。
    """

    model_config = ConfigDict(frozen=True)

    id: ProjectId
    name: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = Field(default_factory=utcnow)
    archived_at: datetime | None = None

    def archive(self, at: datetime | None = None) -> Project:
        """将项目归档；ARCHIVED 是终态，重复归档是幂等空操作。"""
        if self.status == ProjectStatus.ARCHIVED:
            return self
        return self.model_copy(
            update={"status": ProjectStatus.ARCHIVED, "archived_at": at or utcnow()}
        )


class ProjectFile(BaseModel):
    """项目中的逻辑文件；同一逻辑文件的更新创建新的 ProjectFileVersion。"""

    model_config = ConfigDict(frozen=True)

    id: FileId
    project_id: ProjectId
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class ProjectFileVersion(BaseModel):
    """不可变输入版本。

    导入期间为 IMPORTING，可补充内容哈希、大小与格式后定稿；定稿后不可变。
    同一逻辑文件的更新必须创建新的 ProjectFileVersion，禁止覆盖被 Snapshot 引用的版本。
    """

    model_config = ConfigDict(frozen=True)

    id: FileVersionId
    file_id: FileId
    project_id: ProjectId
    version_number: int
    status: FileVersionStatus = FileVersionStatus.IMPORTING
    content_hash: ContentHash | None = None
    byte_size: int | None = None
    media_type: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finalized_at: datetime | None = None

    def mark_ready(
        self,
        content_hash: ContentHash,
        byte_size: int,
        media_type: str,
        at: datetime | None = None,
    ) -> ProjectFileVersion:
        """定稿为 READY，绑定内容哈希、大小与检测到的格式。"""
        self._ensure_importing()
        return self.model_copy(
            update={
                "status": FileVersionStatus.READY,
                "content_hash": content_hash,
                "byte_size": byte_size,
                "media_type": media_type,
                "finalized_at": at or utcnow(),
            }
        )

    def mark_failed(self, at: datetime | None = None) -> ProjectFileVersion:
        """定稿为 FAILED（导入/提取失败）。"""
        self._ensure_importing()
        return self.model_copy(
            update={"status": FileVersionStatus.FAILED, "finalized_at": at or utcnow()}
        )

    def mark_unsupported(self, at: datetime | None = None) -> ProjectFileVersion:
        """定稿为 UNSUPPORTED（格式不支持）。"""
        self._ensure_importing()
        return self.model_copy(
            update={"status": FileVersionStatus.UNSUPPORTED, "finalized_at": at or utcnow()}
        )

    def _ensure_importing(self) -> None:
        """确保版本仍处于可定稿的 IMPORTING 状态，否则视为破坏不可变不变量。"""
        if self.status != FileVersionStatus.IMPORTING:
            raise FileVersionImmutableError(
                f"ProjectFileVersion {self.id} 已定稿为 {self.status}，不可再修改"
            )


class SnapshotEntry(BaseModel):
    """ProjectSnapshot 中单个逻辑文件创建时的固定版本与处理状态。"""

    model_config = ConfigDict(frozen=True)

    file_version_id: FileVersionId
    file_id: FileId
    version_number: int
    status: FileVersionStatus
    content_hash: ContentHash | None = None


class ProjectSnapshot(BaseModel):
    """Run 固定的不可变数据视图。创建后不可变，不提供更新操作。"""

    model_config = ConfigDict(frozen=True)

    id: SnapshotId
    project_id: ProjectId
    created_at: datetime = Field(default_factory=utcnow)
    entries: tuple[SnapshotEntry, ...] = ()
    index_version: str | None = None
    dataset_version_ids: tuple[DatasetId, ...] = ()

    def ready_entries(self) -> tuple[SnapshotEntry, ...]:
        """返回状态为 READY、可供检索与挂载的条目。"""
        return tuple(e for e in self.entries if e.status == FileVersionStatus.READY)

    def entry_for(self, file_version_id: FileVersionId) -> SnapshotEntry | None:
        """按文件版本 ID 查找条目；不存在返回 ``None``。"""
        return next((e for e in self.entries if e.file_version_id == file_version_id), None)


class CoverageItem(BaseModel):
    """ProjectCoverageReport 中单个文件的处理结果。"""

    model_config = ConfigDict(frozen=True)

    file_version_id: FileVersionId
    file_id: FileId
    status: CoverageItemStatus
    detail: str | None = None


class ProjectCoverageReport(BaseModel):
    """FULL_PROJECT 全量枚举的覆盖报告。

    ``has_uncovered`` 为真时（存在 FAILED/UNSUPPORTED/SKIPPED 项），最终回答必须
    明确披露覆盖缺口，不得声称“分析了所有项目文件”。
    """

    model_config = ConfigDict(frozen=True)

    id: CoverageReportId
    snapshot_id: SnapshotId
    created_at: datetime = Field(default_factory=utcnow)
    items: tuple[CoverageItem, ...] = ()

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def processed(self) -> int:
        return sum(1 for i in self.items if i.status == CoverageItemStatus.PROCESSED)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status == CoverageItemStatus.FAILED)

    @property
    def unsupported(self) -> int:
        return sum(1 for i in self.items if i.status == CoverageItemStatus.UNSUPPORTED)

    @property
    def skipped(self) -> int:
        return sum(1 for i in self.items if i.status == CoverageItemStatus.SKIPPED)

    def has_uncovered(self) -> bool:
        """是否存在失败、不支持或跳过项，从而必须在回答中披露覆盖缺口。"""
        return any(i.status != CoverageItemStatus.PROCESSED for i in self.items)
