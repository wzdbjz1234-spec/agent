"""Lineage 领域对象。

血缘记录正式资源之间的来源-目标关系，使用稳定 ID 与内容哈希，不用裸路径替代。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .clock import utcnow
from .ids import ContentHash, LineageId, RunId


class ResourceKind(StrEnum):
    """血缘中的资源类型。"""

    FILE_VERSION = "FILE_VERSION"
    STEP = "STEP"
    DATASET = "DATASET"
    ARTIFACT = "ARTIFACT"
    FINDING = "FINDING"


class ResourceRef(BaseModel):
    """血缘中的一个资源引用。"""

    model_config = ConfigDict(frozen=True)

    kind: ResourceKind
    resource_id: str
    content_hash: ContentHash | None = None


class Lineage(BaseModel):
    """一条来源 -> 目标的血缘记录。"""

    model_config = ConfigDict(frozen=True)

    id: LineageId
    run_id: RunId
    source: ResourceRef
    target: ResourceRef
    created_at: datetime = Field(default_factory=utcnow)
