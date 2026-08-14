"""控制面 API 的请求 DTO。

DTO 只包含稳定 ID、名称、查询参数和有界数据，不把 Runtime SQLite 行、Workspace 路径或
第三方 SDK 类型泄漏到 HTTP 边界。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateProjectRequest(BaseModel):
    """创建 Project 的最小请求。"""

    name: str = Field(min_length=1, max_length=255)


class CreateTaskRequest(BaseModel):
    """创建 Task 时显式固定的 Snapshot。"""

    project_snapshot_id: str = Field(min_length=1, max_length=255)
    session_id: str | None = Field(default=None, min_length=1, max_length=255)


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
