"""AnalysisStep 领域对象。

一次独立、可审计的本地计算。失败 Step 不回到 RUNNING，重试创建新 Step 并通过
``retry_of_step_id`` 关联；``failure_kind`` 仅在 FAILED 状态下存在。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clock import utcnow
from .enums import StepFailureKind, StepStatus
from .ids import RunId, StepId
from .state_machine import check_transition

# Step 迁移表：失败后无出边，重试必须创建新 Step
STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
    StepStatus.RUNNING: frozenset(
        {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.TIMED_OUT, StepStatus.CANCELLED}
    ),
}


class AnalysisStep(BaseModel):
    """一次独立分析步骤。"""

    model_config = ConfigDict(frozen=True)

    id: StepId
    run_id: RunId
    status: StepStatus = StepStatus.PENDING
    failure_kind: StepFailureKind | None = None
    retry_of_step_id: StepId | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _check_failure_kind_consistency(self) -> AnalysisStep:
        """failure_kind 仅在 FAILED 状态下存在，避免非法构造绕过状态机。"""
        if self.status == StepStatus.FAILED and self.failure_kind is None:
            raise ValueError("FAILED 状态必须提供 failure_kind")
        if self.status != StepStatus.FAILED and self.failure_kind is not None:
            raise ValueError("非 FAILED 状态不得携带 failure_kind")
        return self

    def start(self, at: datetime | None = None) -> AnalysisStep:
        """PENDING -> RUNNING。"""
        check_transition(STEP_TRANSITIONS, self.status, StepStatus.RUNNING, "AnalysisStep")
        return self.model_copy(update={"status": StepStatus.RUNNING, "started_at": at or utcnow()})

    def succeed(self, at: datetime | None = None) -> AnalysisStep:
        """RUNNING -> SUCCEEDED。"""
        check_transition(STEP_TRANSITIONS, self.status, StepStatus.SUCCEEDED, "AnalysisStep")
        return self.model_copy(
            update={"status": StepStatus.SUCCEEDED, "finished_at": at or utcnow()}
        )

    def fail(self, kind: StepFailureKind, at: datetime | None = None) -> AnalysisStep:
        """RUNNING -> FAILED，必须提供失败分类。"""
        check_transition(STEP_TRANSITIONS, self.status, StepStatus.FAILED, "AnalysisStep")
        return self.model_copy(
            update={
                "status": StepStatus.FAILED,
                "failure_kind": kind,
                "finished_at": at or utcnow(),
            }
        )

    def timeout(self, at: datetime | None = None) -> AnalysisStep:
        """RUNNING -> TIMED_OUT。"""
        check_transition(STEP_TRANSITIONS, self.status, StepStatus.TIMED_OUT, "AnalysisStep")
        return self.model_copy(
            update={"status": StepStatus.TIMED_OUT, "finished_at": at or utcnow()}
        )

    def cancel(self, at: datetime | None = None) -> AnalysisStep:
        """PENDING/RUNNING -> CANCELLED，幂等：已取消返回自身。"""
        if self.status == StepStatus.CANCELLED:
            return self
        check_transition(STEP_TRANSITIONS, self.status, StepStatus.CANCELLED, "AnalysisStep")
        return self.model_copy(
            update={"status": StepStatus.CANCELLED, "finished_at": at or utcnow()}
        )
