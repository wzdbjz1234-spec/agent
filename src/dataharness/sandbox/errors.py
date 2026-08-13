"""Sandbox 边界对上层稳定暴露的错误分类。"""

from __future__ import annotations


class SandboxError(RuntimeError):
    """所有 Sandbox 执行边界错误的基类。"""


class SandboxPolicyError(SandboxError):
    """规格、挂载或 attestation 不满足安全策略，必须 fail closed。"""


class SandboxLostError(SandboxError):
    """Sandbox 已丢失或被销毁，调用方可用相同 digest 重建新 lease。"""


class SandboxTimeoutError(SandboxError):
    """独立 Step 进程超过请求或 Spec 的时间上限。"""


class SandboxCancelledError(SandboxError):
    """独立 Step 已被取消，Provider 已请求清理残留进程。"""


class SandboxOutputLimitError(SandboxError):
    """stdout/stderr 超过 Spec 上限，完整输出不回传给 Host。"""
