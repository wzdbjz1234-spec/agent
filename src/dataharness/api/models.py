"""控制面 API 的请求 DTO。

DTO 只包含稳定 ID、名称、查询参数和有界数据，不把 Runtime SQLite 行、Workspace 路径或
第三方 SDK 类型泄漏到 HTTP 边界。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataharness.domain import Artifact, ConversationMessage, Dataset, Finding, Lineage


class CreateProjectRequest(BaseModel):
    """创建 Project 的最小请求。"""

    name: str = Field(min_length=1, max_length=255)


class CreateSessionRequest(BaseModel):
    """创建固定属于当前 Project 的连续对话 Session。"""

    label: str | None = Field(default=None, min_length=1, max_length=255)


class SendMessageRequest(BaseModel):
    """发送一条普通对话消息；它不会隐式创建 Task/Run。"""

    content: str = Field(min_length=1, max_length=200_000)
    # 只保存用户可见的 user/assistant 消息，不保存隐藏思考、工具载荷或原始模型请求。
    persist: bool = True


class ConversationResponse(BaseModel):
    """自然语言聊天结果与可选的分析输入边界。"""

    user: ConversationMessage
    assistant: ConversationMessage
    snapshot_id: str | None = None
    analysis_job: dict[str, str] | None = None


class CreateTaskRequest(BaseModel):
    """创建 Task 时固定 Snapshot，并接收用户问题的受控载荷。"""

    project_snapshot_id: str = Field(min_length=1, max_length=255)
    session_id: str | None = Field(default=None, min_length=1, max_length=255)
    prompt: str | None = Field(default=None, min_length=1, max_length=100_000)


class RetryTaskRequest(BaseModel):
    """重试仍为显式 Snapshot 选择，防止偷偷切换到项目最新文件。"""

    project_snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)


class ApiErrorBody(BaseModel):
    """统一错误 DTO；不回显异常正文、请求原文或隐私映射。"""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    trace_id: str | None = None


class ApiErrorResponse(BaseModel):
    """所有 API 错误的稳定外层。"""

    model_config = ConfigDict(frozen=True)

    error: ApiErrorBody


class TaskAnswer(BaseModel):
    """Task 的最终结构化回答视图。

    回答只由 Runtime 中的 Finding、正式资源和 lineage 组成；不把 Workspace 路径、
    SQLite 行或模型原始消息直接暴露到 HTTP 边界。``disclosures`` 仅保存覆盖缺口等
    稳定审计提示，调用方可以据此区分“已有结论”和“仍需人工复核”。
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    task_status: str
    answer: str | None = None
    run_ids: tuple[str, ...]
    findings: tuple[Finding, ...]
    datasets: tuple[Dataset, ...]
    artifacts: tuple[Artifact, ...]
    lineage: tuple[Lineage, ...]
    disclosures: tuple[str, ...] = ()
