"""Session 领域对象。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .clock import utcnow
from .ids import ProjectId, SessionId


class Session(BaseModel):
    """长期用户上下文。

    V1 中只承载轻量元数据，不持有对话历史或工作记忆——对话事实源是
    PydanticAI checkpoint，跨步骤数据写入 Workspace。

    ``project_id`` 在 Phase 11 中用于建立数据库级的历史作用域。为了让 Phase 00–10
    的手工构造记录仍能迁移和读取，这个字段暂时允许为空；所有新的 API 创建路径都会
    显式写入 Project ID，并在创建 Task 时再次校验归属。
    """

    model_config = ConfigDict(frozen=True)

    id: SessionId
    project_id: ProjectId | None = None
    label: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
