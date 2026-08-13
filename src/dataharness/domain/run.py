"""Run 领域对象。

Run 表示一次执行尝试。``project_snapshot_id`` 为必填且不可变，创建后固定数据视图；
恢复同一 Run 不得切换到最新文件。``status`` 表达生命周期，``phase`` 表达当前工作。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clock import utcnow
from .enums import RunPhase, RunStatus, WaitReason
from .errors import IllegalStateTransitionError, InvalidStateError
from .ids import ProjectId, RunId, SnapshotId, TaskId
from .state_machine import check_transition

# Run 生命周期迁移表：终态（SUCCEEDED/FAILED/CANCELLED）无出边
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.WAITING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.WAITING: frozenset({RunStatus.RUNNING}),
}

# Run 工作阶段迁移表：只向前推进
RUN_PHASE_TRANSITIONS: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.PREPARING: frozenset({RunPhase.REASONING}),
    RunPhase.REASONING: frozenset({RunPhase.EXECUTING}),
    RunPhase.EXECUTING: frozenset({RunPhase.VERIFYING}),
    RunPhase.VERIFYING: frozenset({RunPhase.FINALIZING}),
}


class Run(BaseModel):
    """一次执行尝试，固定 ``project_snapshot_id``。"""

    model_config = ConfigDict(frozen=True)

    id: RunId
    task_id: TaskId
    project_id: ProjectId
    project_snapshot_id: SnapshotId
    status: RunStatus = RunStatus.QUEUED
    phase: RunPhase = RunPhase.PREPARING
    wait_reason: WaitReason | None = None
    # 取消请求是一个独立的耐久意图。RUNNING Run 不能在调用方线程中直接
    # 改成终态，否则 worker 仍可能把正在执行的 Sandbox 结果提交回来；先记录
    # 这个时间戳，worker 在确认 fencing token 后负责停止新调用、清理并收口状态。
    cancel_requested_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _check_wait_reason_consistency(self) -> Run:
        """wait_reason 与 WAITING 状态必须一致。"""
        if self.status == RunStatus.WAITING and self.wait_reason is None:
            raise ValueError("WAITING 状态必须提供 wait_reason")
        if self.status != RunStatus.WAITING and self.wait_reason is not None:
            raise ValueError("非 WAITING 状态不得携带 wait_reason")
        return self

    def _require_from(self, allowed: frozenset[RunStatus], action: str) -> None:
        """校验操作只允许从特定来源状态执行，避免 start 与 resume 语义混用。"""
        if self.status not in allowed:
            pretty = ", ".join(str(s) for s in sorted(allowed, key=str))
            raise IllegalStateTransitionError(
                f"Run.{action} 不允许从 {self.status} 执行；允许来源：{pretty}"
            )

    def start(self, at: datetime | None = None) -> Run:
        """QUEUED -> RUNNING。"""
        self._require_from(frozenset({RunStatus.QUEUED}), "start")
        return self.model_copy(update={"status": RunStatus.RUNNING, "updated_at": at or utcnow()})

    def wait(self, reason: WaitReason, at: datetime | None = None) -> Run:
        """RUNNING -> WAITING，必须提供等待原因。"""
        check_transition(RUN_TRANSITIONS, self.status, RunStatus.WAITING, "Run")
        return self.model_copy(
            update={
                "status": RunStatus.WAITING,
                "wait_reason": reason,
                "updated_at": at or utcnow(),
            }
        )

    def resume(self, at: datetime | None = None) -> Run:
        """WAITING -> RUNNING，清除等待原因。"""
        self._require_from(frozenset({RunStatus.WAITING}), "resume")
        return self.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "wait_reason": None,
                "updated_at": at or utcnow(),
            }
        )

    def succeed(self, at: datetime | None = None) -> Run:
        """RUNNING -> SUCCEEDED。"""
        check_transition(RUN_TRANSITIONS, self.status, RunStatus.SUCCEEDED, "Run")
        now = at or utcnow()
        return self.model_copy(
            update={"status": RunStatus.SUCCEEDED, "updated_at": now, "completed_at": now}
        )

    def fail(self, at: datetime | None = None) -> Run:
        """RUNNING -> FAILED。"""
        check_transition(RUN_TRANSITIONS, self.status, RunStatus.FAILED, "Run")
        now = at or utcnow()
        return self.model_copy(
            update={"status": RunStatus.FAILED, "updated_at": now, "completed_at": now}
        )

    def cancel(self, at: datetime | None = None) -> Run:
        """QUEUED/RUNNING -> CANCELLED，幂等：已取消返回自身。"""
        if self.status == RunStatus.CANCELLED:
            return self
        check_transition(RUN_TRANSITIONS, self.status, RunStatus.CANCELLED, "Run")
        now = at or utcnow()
        return self.model_copy(
            update={
                "status": RunStatus.CANCELLED,
                "cancel_requested_at": self.cancel_requested_at or now,
                "updated_at": now,
                "completed_at": now,
            }
        )

    def request_cancel(self, at: datetime | None = None) -> Run:
        """记录取消意图但不改变生命周期状态。

        运行中的 Run 仍需要 worker 负责终止外部副作用，因此取消请求和终态收口
        分成两个持久化动作；重复请求保持同一首次请求时间，便于幂等审计。
        """
        if self.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            return self
        if self.cancel_requested_at is not None:
            return self
        now = at or utcnow()
        return self.model_copy(update={"cancel_requested_at": now, "updated_at": now})

    def advance_phase(self, target: RunPhase, at: datetime | None = None) -> Run:
        """向前推进工作阶段；阶段只能在 RUNNING 状态下推进。"""
        if self.status != RunStatus.RUNNING:
            raise InvalidStateError(f"Run 只能在 RUNNING 状态下推进 phase，当前为 {self.status}")
        check_transition(RUN_PHASE_TRANSITIONS, self.phase, target, "Run.phase")
        return self.model_copy(update={"phase": target, "updated_at": at or utcnow()})
