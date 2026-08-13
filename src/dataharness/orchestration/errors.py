"""Durable orchestration 对外错误与错误分类。"""

from __future__ import annotations

from dataharness.sandbox import (
    SandboxLostError,
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxTimeoutError,
)

from .models import FailureClass


class OrchestrationError(RuntimeError):
    """可被 worker 分类并写入 Runtime SQLite 的执行错误。"""

    failure_class = FailureClass.INTERNAL_ERROR
    retryable = False


class ModelCorrectableError(OrchestrationError):
    """模型生成的请求可通过有限重试修正。"""

    failure_class = FailureClass.MODEL_CORRECTABLE
    retryable = True


class ResourceLimitError(OrchestrationError):
    """超时、输出或资源配额耗尽。"""

    failure_class = FailureClass.RESOURCE_LIMIT
    retryable = True


class PolicyDeniedError(OrchestrationError):
    """策略拒绝；默认不自动重试，避免重复触发同一安全拒绝。"""

    failure_class = FailureClass.POLICY_DENIED
    retryable = False


class BudgetExhaustedError(OrchestrationError):
    """预算耗尽，Run 进入 WAITING 而不是静默失败。"""

    failure_class = FailureClass.BUDGET_EXHAUSTED
    retryable = False


class HostCrashError(OrchestrationError):
    """handler 在恢复前崩溃，重新领取同一个 Run。"""

    failure_class = FailureClass.HOST_CRASH
    retryable = True


class RetryLimitExceeded(OrchestrationError):
    """自动重试已达到策略上限，必须终止而不能无限循环。"""

    failure_class = FailureClass.INTERNAL_ERROR
    retryable = False


def classify_error(error: BaseException) -> tuple[FailureClass, bool]:
    """把 handler/Provider 的稳定错误映射到重试策略。

    Sandbox 错误在这里统一分类，编排层不让上层依赖 Provider 的具体实现类；策略拒绝
    与预算耗尽保持 fail-closed，只有模型可修正、资源暂时不足和 Sandbox 丢失允许有限重试。
    """
    if isinstance(error, OrchestrationError):
        return error.failure_class, error.retryable
    if isinstance(error, (SandboxTimeoutError, SandboxOutputLimitError)):
        return FailureClass.RESOURCE_LIMIT, True
    if isinstance(error, SandboxLostError):
        return FailureClass.SANDBOX_LOST, True
    if isinstance(error, SandboxPolicyError):
        return FailureClass.POLICY_DENIED, False
    return FailureClass.INTERNAL_ERROR, False
