"""storage 模块的窄值对象。

这些对象只携带并发控制与审计元数据，不暴露 SQLite row、cursor 或连接。
"""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from dataharness.domain import ContentHash, Run, RunId

T = TypeVar("T")


class StoredRecord[T](BaseModel):
    """领域对象及其 CAS 版本；保存时把 ``version`` 作为预期版本传回。"""

    model_config = ConfigDict(frozen=True)

    value: T
    version: int


class RunLease(BaseModel):
    """Worker 对单个 Run 的带 fencing token 租约。"""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    owner: str
    epoch: int
    expires_at: datetime
    heartbeat_at: datetime


class ClaimedRun(BaseModel):
    """一次原子 queue claim 的结果。"""

    model_config = ConfigDict(frozen=True)

    run: Run
    version: int
    lease: RunLease
    recovered: bool


class EventRecord(BaseModel):
    """追加式领域/耐久事件；payload 只能保存脱敏元数据。"""

    model_config = ConfigDict(frozen=True)

    id: int
    aggregate_type: str
    aggregate_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, object]


class CheckpointMetadata(BaseModel):
    """PydanticAI checkpoint 的定位元数据，不包含模型消息或原始载荷。"""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: RunId
    sequence: int
    checkpoint_ref: str
    content_hash: ContentHash
    created_at: datetime


class IdempotencyRecord(BaseModel):
    """幂等请求摘要及可选结果引用。"""

    model_config = ConfigDict(frozen=True)

    scope: str
    key: str
    request_hash: ContentHash
    result_ref: str | None
    created_at: datetime
