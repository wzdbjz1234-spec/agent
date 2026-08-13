"""持久编排层的窄值对象。

这些类型只表达 worker 如何收口一次 Run，不承载模型消息、Sandbox SDK 对象或宿主
路径；大结果和对话上下文仍分别属于 Workspace 与 PydanticAI checkpoint。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from dataharness.domain import RunPhase, WaitReason
from dataharness.storage import CheckpointMetadata


class ExecutionDecision(StrEnum):
    """Run handler 返回的生命周期决策。"""

    SUCCEEDED = "SUCCEEDED"
    WAITING = "WAITING"


class RecoveryDecision(StrEnum):
    """worker 领取过期 Run 后选择的恢复入口。"""

    START_FROM_BEGINNING = "START_FROM_BEGINNING"
    RESUME_FROM_CHECKPOINT = "RESUME_FROM_CHECKPOINT"
    REBUILD_SANDBOX = "REBUILD_SANDBOX"
    CANCEL = "CANCEL"
    TERMINAL = "TERMINAL"


class FailureClass(StrEnum):
    """自动重试与最终收口共用的失败分类。"""

    MODEL_CORRECTABLE = "MODEL_CORRECTABLE"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    SANDBOX_LOST = "SANDBOX_LOST"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    HOST_CRASH = "HOST_CRASH"
    POLICY_DENIED = "POLICY_DENIED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RunOutcome(BaseModel):
    """Run handler 的结构化结果；不允许用普通字符串伪造 WAITING 原因。"""

    model_config = ConfigDict(frozen=True)

    decision: ExecutionDecision
    phase: RunPhase | None = None
    wait_reason: WaitReason | None = None
    checkpoint: CheckpointMetadata | None = None

    @model_validator(mode="after")
    def _check_wait_reason(self) -> RunOutcome:
        if self.decision == ExecutionDecision.WAITING and self.wait_reason is None:
            raise ValueError("WAITING outcome 必须提供 wait_reason")
        if self.decision == ExecutionDecision.SUCCEEDED and self.wait_reason is not None:
            raise ValueError("SUCCEEDED outcome 不得携带 wait_reason")
        return self
