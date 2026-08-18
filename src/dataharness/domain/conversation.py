"""Conversation 消息值对象。

消息是用户与 Agent 的交互记录，不是后台任务。普通对话可以选择持久化；只有
涉及长时计算时才会另外创建 AnalysisJob/Execution 事实。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .clock import utcnow
from .ids import MessageId, ProjectId, SessionId


class MessageRole(StrEnum):
    """对话消息的最小角色集合；模型内部工具消息不直接暴露给聊天 API。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationMessage(BaseModel):
    """属于 Project/Session 的一条有界文本消息。"""

    model_config = ConfigDict(frozen=True)

    id: MessageId
    project_id: ProjectId
    session_id: SessionId
    role: MessageRole
    content: str = Field(min_length=1, max_length=200_000)
    created_at: datetime = Field(default_factory=utcnow)
