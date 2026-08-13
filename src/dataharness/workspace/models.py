"""Workspace 资源引用与可恢复发布值对象。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dataharness.domain import ContentHash, ProjectId, RunId, StepId, TaskId


class PublicationKind(StrEnum):
    """正式输出种类；决定资源进入 datasets 或 artifacts。"""

    DATASET = "DATASET"
    ARTIFACT = "ARTIFACT"


class PublicationStatus(StrEnum):
    """跨 SQLite 与文件系统发布协议的持久状态。"""

    STAGED = "STAGED"
    AVAILABLE = "AVAILABLE"
    CORRUPT = "CORRUPT"


class WorkspaceResource(BaseModel):
    """不暴露宿主绝对路径的稳定 Workspace 资源引用。"""

    model_config = ConfigDict(frozen=True)

    project_id: ProjectId
    namespace: str
    resource_id: str
    name: str
    content_hash: ContentHash
    byte_size: int


class PublicationRecord(BaseModel):
    """正式输出发布记录；幂等键固定为 run/step/output_name。"""

    model_config = ConfigDict(frozen=True)

    idempotency_key: str
    project_id: ProjectId
    task_id: TaskId
    run_id: RunId
    step_id: StepId
    output_name: str
    kind: PublicationKind
    resource_id: str
    content_hash: ContentHash
    byte_size: int
    status: PublicationStatus
    created_at: datetime
    updated_at: datetime
    detail: str | None = None
