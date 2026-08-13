"""正式资源领域对象：Dataset 与 Artifact。

正式资源是发布后的不可变记录，引用稳定 ID 与内容哈希，不用裸路径替代。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .clock import utcnow
from .ids import ArtifactId, ContentHash, DatasetId, ProjectId, RunId, TaskId


class Dataset(BaseModel):
    """项目级正式派生数据集。"""

    model_config = ConfigDict(frozen=True)

    id: DatasetId
    project_id: ProjectId
    name: str
    content_hash: ContentHash
    task_id: TaskId | None = None
    run_id: RunId | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(BaseModel):
    """项目级正式展示产物。"""

    model_config = ConfigDict(frozen=True)

    id: ArtifactId
    project_id: ProjectId
    name: str
    content_hash: ContentHash
    task_id: TaskId | None = None
    run_id: RunId | None = None
    created_at: datetime = Field(default_factory=utcnow)
