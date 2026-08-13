"""Task 领域对象。

Task 表示绑定单一 Project 的用户目标。每个分析 Task 必须绑定一个 Project，
不能跨 Project 读取数据。等待细节统一用 ``wait_reason`` 表达，无 SUSPENDED。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clock import utcnow
from .enums import TaskStatus, WaitReason
from .errors import IllegalStateTransitionError
from .ids import ProjectId, SessionId, TaskId
from .state_machine import check_transition

# Task 迁移表：终态（COMPLETED/FAILED/CANCELLED）无出边，不可回退
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.ACTIVE, TaskStatus.CANCELLED}),
    TaskStatus.ACTIVE: frozenset(
        {TaskStatus.WAITING, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING: frozenset({TaskStatus.ACTIVE}),
}


class Task(BaseModel):
    """一次用户目标。``project_id`` 为必填，强制绑定单一 Project。"""

    model_config = ConfigDict(frozen=True)

    id: TaskId
    project_id: ProjectId
    session_id: SessionId | None = None
    status: TaskStatus = TaskStatus.QUEUED
    wait_reason: WaitReason | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _check_wait_reason_consistency(self) -> Task:
        """wait_reason 与 WAITING 状态必须一致，避免非法构造绕过状态机。"""
        if self.status == TaskStatus.WAITING and self.wait_reason is None:
            raise ValueError("WAITING 状态必须提供 wait_reason")
        if self.status != TaskStatus.WAITING and self.wait_reason is not None:
            raise ValueError("非 WAITING 状态不得携带 wait_reason")
        return self

    def _require_from(self, allowed: frozenset[TaskStatus], action: str) -> None:
        """校验操作只允许从特定来源状态执行，避免 start 与 resume 语义混用。"""
        if self.status not in allowed:
            pretty = ", ".join(str(s) for s in sorted(allowed, key=str))
            raise IllegalStateTransitionError(
                f"Task.{action} 不允许从 {self.status} 执行；允许来源：{pretty}"
            )

    def start(self, at: datetime | None = None) -> Task:
        """QUEUED -> ACTIVE。"""
        self._require_from(frozenset({TaskStatus.QUEUED}), "start")
        return self.model_copy(update={"status": TaskStatus.ACTIVE, "updated_at": at or utcnow()})

    def wait(self, reason: WaitReason, at: datetime | None = None) -> Task:
        """ACTIVE -> WAITING，必须提供等待原因。"""
        check_transition(TASK_TRANSITIONS, self.status, TaskStatus.WAITING, "Task")
        return self.model_copy(
            update={
                "status": TaskStatus.WAITING,
                "wait_reason": reason,
                "updated_at": at or utcnow(),
            }
        )

    def resume(self, at: datetime | None = None) -> Task:
        """WAITING -> ACTIVE，清除等待原因。"""
        self._require_from(frozenset({TaskStatus.WAITING}), "resume")
        return self.model_copy(
            update={
                "status": TaskStatus.ACTIVE,
                "wait_reason": None,
                "updated_at": at or utcnow(),
            }
        )

    def complete(self, at: datetime | None = None) -> Task:
        """ACTIVE -> COMPLETED。"""
        check_transition(TASK_TRANSITIONS, self.status, TaskStatus.COMPLETED, "Task")
        now = at or utcnow()
        return self.model_copy(
            update={"status": TaskStatus.COMPLETED, "updated_at": now, "completed_at": now}
        )

    def fail(self, at: datetime | None = None) -> Task:
        """ACTIVE -> FAILED。"""
        check_transition(TASK_TRANSITIONS, self.status, TaskStatus.FAILED, "Task")
        now = at or utcnow()
        return self.model_copy(
            update={"status": TaskStatus.FAILED, "updated_at": now, "completed_at": now}
        )

    def cancel(self, at: datetime | None = None) -> Task:
        """QUEUED/ACTIVE -> CANCELLED，幂等：已取消返回自身。"""
        if self.status == TaskStatus.CANCELLED:
            return self
        check_transition(TASK_TRANSITIONS, self.status, TaskStatus.CANCELLED, "Task")
        now = at or utcnow()
        return self.model_copy(
            update={"status": TaskStatus.CANCELLED, "updated_at": now, "completed_at": now}
        )
