"""Agent 执行诊断日志的脱敏与有界输出测试。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import cast

from dataharness.agent.diagnostics import log_model_output, log_tool_call, log_tool_result
from dataharness.domain import TaskId
from dataharness.privacy import ModelGateway


class _Gateway:
    """只返回输入的最小日志脱敏 fake。"""

    def sanitize_log(self, _task_id, text: str):
        return SimpleNamespace(cloud_text=text)


def _gateway() -> ModelGateway:
    return cast(ModelGateway, _Gateway())


def test_model_output_logs_reasoning_and_tool_call(caplog) -> None:
    caplog.set_level(logging.INFO, logger="dataharness.execution")

    log_model_output(
        _gateway(),
        TaskId("task-1"),
        '{"tool_call":{"name":"search_project","args":{"query":"alpha"}},'
        '"reasoning":"先查找相关文件"}',
    )

    assert '"event": "MODEL_OUTPUT"' in caplog.text
    assert '"kind": "TOOL_CALL"' in caplog.text
    assert '"reasoning": "先查找相关文件"' in caplog.text
    assert '"tool": "search_project"' in caplog.text


def test_tool_diagnostics_are_bounded(caplog) -> None:
    caplog.set_level(logging.INFO, logger="dataharness.execution")

    long_text = "x" * 20_000
    log_tool_call(_gateway(), TaskId("task-1"), "execute_python", {"code": long_text})
    log_tool_result(_gateway(), TaskId("task-1"), "execute_python", {"stdout": long_text})

    assert "<truncated>" not in caplog.text
    assert len(caplog.text) < 20_000
