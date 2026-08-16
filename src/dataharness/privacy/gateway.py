"""所有云模型 Adapter 的唯一调用入口。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dataharness.domain import TaskId

from .policy import BoundaryKind, PreparedRequest, PrivacyPolicy


@runtime_checkable
class CloudModelProvider(Protocol):
    """云模型 Adapter 的最小协议；Adapter 只能接收已脱敏的文本。"""

    def complete(self, request: str) -> str:
        """发送已由 ModelGateway 处理的请求并返回模型文本。"""
        ...


class ModelProviderError(RuntimeError):
    """Provider 失败后的稳定错误；消息只包含分类，不包含请求、响应或密钥。"""

    def __init__(self, message: str, *, code: str = "MODEL_PROVIDER_ERROR") -> None:
        self.code = code
        super().__init__(message)


class ModelGateway:
    """请求云模型的唯一出口，并统一处理回复、异常和辅助文本。"""

    def __init__(self, provider: CloudModelProvider, policy: PrivacyPolicy) -> None:
        self._provider = provider
        self._policy = policy

    def complete(self, task_id: TaskId, request: str) -> PreparedRequest:
        """在 Provider 调用前阻断 secret、占位 PII，且再次脱敏模型回复。"""
        prepared = self._policy.prepare_request(task_id, request)
        try:
            response = self._provider.complete(prepared.cloud_text)
        except ModelProviderError:
            # 生产 Provider 已经把 HTTP 状态、配置缺失和响应损坏映射成稳定分类；
            # 这里不能再把底层异常正文包装回调用方。
            raise
        except Exception as error:
            safe = self._policy.sanitize_boundary_text(task_id, str(error), BoundaryKind.EXCEPTION)
            raise ModelProviderError(safe.cloud_text) from error
        return self._policy.sanitize_boundary_text(task_id, response, BoundaryKind.RESPONSE)

    def sanitize_tool_result(self, task_id: TaskId, text: str) -> PreparedRequest:
        """供工具结果写入模型消息、日志前统一再扫描。"""
        return self._policy.sanitize_boundary_text(task_id, text, BoundaryKind.TOOL_RESULT)

    def sanitize_compaction(self, task_id: TaskId, text: str) -> PreparedRequest:
        """供摘要/压缩内容写入 checkpoint 前统一再扫描。"""
        return self._policy.sanitize_boundary_text(task_id, text, BoundaryKind.COMPACTION)

    def sanitize_log(self, task_id: TaskId, text: str) -> PreparedRequest:
        """供日志和 trace exporter 使用的统一再扫描入口。"""
        return self._policy.sanitize_boundary_text(task_id, text, BoundaryKind.LOG)

    def sanitize_trace(self, task_id: TaskId, text: str) -> PreparedRequest:
        """供 trace 属性字符串使用的统一再扫描入口。"""
        return self._policy.sanitize_boundary_text(task_id, text, BoundaryKind.TRACE)

    def restore_tool_input(
        self, task_id: TaskId, text: str, *, allowed_kinds: tuple[str, ...]
    ) -> str:
        """仅在 Sandbox 工具调用前恢复当前 Task 已登记且类型允许的 PII。"""
        return self._policy.restore_tool_input(task_id, text, allowed_kinds=allowed_kinds)
