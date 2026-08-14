"""OpenTelemetry 适配器。

本模块只把稳定 ID、hash、大小、状态、耗时和错误分类送入观测后端。原始 prompt、响应、
stdout/stderr 与异常正文必须先经过 ModelGateway 的 TRACE 脱敏出口；脱敏失败会继续抛出，
而 OpenTelemetry 后端本身不可用时只记录本地告警，不改变业务状态。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Tracer

from dataharness.domain import TaskId
from dataharness.privacy import ModelGateway

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ObservationContext:
    """跨模型、工具和 Sandbox 的关联 ID；不承载原始业务内容。"""

    task_id: TaskId | None = None
    run_id: str | None = None
    step_id: str | None = None
    tool_call_id: str | None = None
    sandbox_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """测试与本地告警可读取的脱敏观测摘要。"""

    name: str
    context: ObservationContext
    attributes: dict[str, str | int | float | bool]


class ObservabilityPrivacyError(RuntimeError):
    """没有可用隐私出口时拒绝记录潜在原始内容。"""


class OpenTelemetryAdapter:
    """容错的 OpenTelemetry Adapter。

    ``gateway`` 是可选的，只有在记录文本字段时才需要它；生产装配应始终提供，
    这样任何日志/trace 文本都不能绕过既有隐私策略。``records`` 方便无 exporter 的
    本地验收和单元测试，不会保存未脱敏正文。
    """

    _SAFE_ATTRIBUTE_KEYS = frozenset(
        {
            "content_hash",
            "size",
            "status",
            "duration_ms",
            "error_class",
            "warning_count",
            "output_count",
            "message",
        }
    )

    def __init__(
        self, gateway: ModelGateway | None = None, *, tracer: Tracer | None = None
    ) -> None:
        self._gateway = gateway
        self._tracer = tracer or trace.get_tracer("dataharness")
        self.records: list[ObservationRecord] = []

    @contextmanager
    def span(
        self,
        name: str,
        context: ObservationContext,
        attributes: Mapping[str, str | int | float | bool] | None = None,
    ) -> Iterator[Span | None]:
        """创建关联 Span；后端故障退化为空上下文，不阻断业务。"""
        safe = self._sanitize_attributes(context, attributes or {})
        try:
            manager = self._tracer.start_as_current_span(name)
            current = manager.__enter__()
        except Exception:
            # exporter、collector 或 SDK 故障不能让已完成的业务事务回滚；不打印异常正文，
            # 避免三方 SDK 的错误对象反而把请求内容带入日志。
            logger.warning("OpenTelemetry 后端不可用，已丢弃一条观测记录")
            yield None
            return

        try:
            span_context = current.get_span_context()
            enriched = dict(safe)
            enriched.update(self._id_attributes(context))
            if span_context.trace_id:
                enriched["trace_id"] = format(span_context.trace_id, "032x")
            current.set_attributes(enriched)
            self.records.append(
                ObservationRecord(
                    name=name,
                    context=context,
                    attributes={str(key): _attribute(value) for key, value in enriched.items()},
                )
            )
        except Exception:
            with _suppress_observability_error(manager):
                pass
            logger.warning("OpenTelemetry 后端不可用，已丢弃一条观测记录")
            yield None
            return

        try:
            yield current
        except BaseException as error:
            with _suppress_observability_error(manager, type(error), error, error.__traceback__):
                pass
            raise
        else:
            with _suppress_observability_error(manager):
                pass

    def record(
        self,
        name: str,
        context: ObservationContext,
        attributes: Mapping[str, str | int | float | bool] | None = None,
    ) -> None:
        """记录一次非嵌套观测；文本属性在进入 OTel 前统一脱敏。"""
        with self.span(name, context, attributes):
            pass

    def _sanitize_attributes(
        self,
        context: ObservationContext,
        attributes: Mapping[str, str | int | float | bool],
    ) -> dict[str, str | int | float | bool]:
        safe: dict[str, str | int | float | bool] = {}
        for key, value in attributes.items():
            if key not in self._SAFE_ATTRIBUTE_KEYS:
                continue
            if key == "message":
                if context.task_id is None or self._gateway is None:
                    raise ObservabilityPrivacyError("观测文本必须绑定 Task 和 ModelGateway")
                # 这一调用故意位于后端 try 块之前：隐私处理失败必须 fail closed。
                safe[key] = self._gateway.sanitize_trace(context.task_id, str(value)).cloud_text
            else:
                safe[key] = value
        return safe

    @staticmethod
    def _id_attributes(context: ObservationContext) -> dict[str, str]:
        """只把稳定关联 ID 写入 Span，绝不把本地路径或 Privacy 映射写入属性。"""
        return {
            key: value
            for key, value in {
                "task_id": str(context.task_id) if context.task_id else None,
                "run_id": context.run_id,
                "step_id": context.step_id,
                "tool_call_id": context.tool_call_id,
                "sandbox_id": context.sandbox_id,
            }.items()
            if value is not None
        }


def _attribute(value: Any) -> str | int | float | bool:
    """把属性限制为 OTel 支持的标量；未知对象只保留类型名。"""
    return value if isinstance(value, (str, int, float, bool)) else type(value).__name__


@contextmanager
def _suppress_observability_error(manager, *exit_args: object) -> Iterator[None]:
    """关闭 Span 时屏蔽 exporter 故障，但不屏蔽业务生成器本身的异常。"""
    try:
        manager.__exit__(*exit_args)
    except Exception:
        logger.warning("OpenTelemetry Span 关闭失败")
    yield
