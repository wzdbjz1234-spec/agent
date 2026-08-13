"""领域状态与阶段枚举。

所有枚举继承 :class:`enum.StrEnum`，便于序列化、日志与数据库持久化时直接使用
字符串值，同时保留类型安全与可读的成员名。
"""

from __future__ import annotations

from enum import StrEnum


class FileVersionStatus(StrEnum):
    """ProjectFileVersion 的处理状态。定稿（非 IMPORTING）后不可变。"""

    IMPORTING = "IMPORTING"
    READY = "READY"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class ProjectStatus(StrEnum):
    """Project 生命周期状态。ARCHIVED 为终态，归档不删除历史 Snapshot。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class CoverageItemStatus(StrEnum):
    """ProjectCoverageReport 中单个文件的覆盖结果。"""

    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    SKIPPED = "SKIPPED"


class TaskStatus(StrEnum):
    """Task 生命周期状态。等待细节用 wait_reason 表达，无 SUSPENDED。"""

    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WaitReason(StrEnum):
    """Task/Run 进入 WAITING 的原因。"""

    USER_INPUT = "USER_INPUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    RETRY_APPROVAL = "RETRY_APPROVAL"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"


class RunStatus(StrEnum):
    """Run 生命周期状态。终态 Run 永不重新打开，用户重试创建新 Run。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunPhase(StrEnum):
    """Run 当前工作阶段，独立于生命周期状态，只向前推进。"""

    PREPARING = "PREPARING"
    REASONING = "REASONING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    FINALIZING = "FINALIZING"


class StepStatus(StrEnum):
    """AnalysisStep 生命周期状态。失败 Step 不回到 RUNNING，重试创建新 Step。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class StepFailureKind(StrEnum):
    """AnalysisStep 失败分类，用于决定重试与熔断策略。"""

    MODEL_CORRECTABLE = "MODEL_CORRECTABLE"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    POLICY_DENIED = "POLICY_DENIED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class FindingStatus(StrEnum):
    """Finding 正式状态。只有 Host Verification Gate 能改变正式状态。"""

    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    WARNING = "WARNING"
    REJECTED = "REJECTED"
