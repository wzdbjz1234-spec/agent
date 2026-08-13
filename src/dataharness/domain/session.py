"""Session 领域对象。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .clock import utcnow
from .ids import SessionId


class Session(BaseModel):
    """长期用户上下文。

    V1 中只承载轻量元数据，不持有对话历史或工作记忆——对话事实源是
    PydanticAI checkpoint，跨步骤数据写入 Workspace。
    """

    model_config = ConfigDict(frozen=True)

    id: SessionId
    label: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
