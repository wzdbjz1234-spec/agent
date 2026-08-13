"""领域错误层级。

所有领域错误继承 :class:`DomainError`，调用方可以统一捕获并映射为稳定错误码。
非法状态迁移是最常见的错误类型，单独定义以便测试与上层精确断言。
"""

from __future__ import annotations


class DomainError(Exception):
    """所有领域错误的基类。"""


class IllegalStateTransitionError(DomainError):
    """尝试执行非法状态迁移（例如从终态再次迁移）。"""


class InvalidStateError(DomainError):
    """对象当前状态不满足某操作的前置条件。"""


class FileVersionImmutableError(DomainError):
    """尝试修改已定稿（非 IMPORTING）的 ProjectFileVersion。"""


class InvalidEvidenceError(DomainError):
    """FindingCandidate 缺少有效证据引用。"""
