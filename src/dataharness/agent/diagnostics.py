"""Agent 执行诊断日志。

这里只记录 Agent 执行链路中对定位问题有用的内容：模型输出、工具调用、工具结果
摘要、模型错误和重试。所有文本先经过 ModelGateway 的 ``sanitize_log``，并在写日志
前做递归截断；HTTP access log、完整请求体、密钥和任意 Host 路径不属于本模块。
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

from dataharness.domain import RunId, TaskId
from dataharness.privacy import ModelGateway

_LOGGER = logging.getLogger("dataharness.execution")
_MAX_TEXT = 4000
_MAX_ITEMS = 20


def configure_execution_logging() -> None:
    """为 Worker 配置单独的执行日志流，不打开 API HTTP access log。"""
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    if _LOGGER.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOGGER.addHandler(handler)


def log_model_output(
    gateway: ModelGateway,
    task_id: TaskId,
    raw_text: str,
    *,
    run_id: RunId | None = None,
) -> None:
    """记录一次已脱敏的模型输出，区分思考、工具调用和最终回答。"""
    safe_text = _sanitize(gateway, task_id, raw_text)
    payload = _try_json(safe_text)
    reasoning = _first_text(payload, "reasoning", "reasoning_content", "thought")
    tool_call = payload.get("tool_call") if isinstance(payload, dict) else None
    if isinstance(tool_call, dict) and isinstance(tool_call.get("name"), str):
        event: dict[str, Any] = {
            "event": "MODEL_OUTPUT",
            "kind": "TOOL_CALL",
            "task_id": str(task_id),
            "tool": tool_call["name"],
            "arguments": _bound(tool_call.get("args", {})),
        }
        if reasoning:
            event["reasoning"] = _bound(reasoning)
    else:
        content = _first_text(payload, "content", "answer", "text")
        if content is None:
            content = safe_text
        else:
            nested = _try_json(content)
            nested_answer = _first_text(nested, "answer", "content", "text")
            if nested_answer:
                content = nested_answer
        event = {
            "event": "MODEL_OUTPUT",
            "kind": "ANSWER",
            "task_id": str(task_id),
            "answer": _bound(content),
        }
        if reasoning:
            event["reasoning"] = _bound(reasoning)
    _emit(event, run_id=run_id)


def log_model_error(
    task_id: TaskId,
    *,
    run_id: RunId | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
) -> None:
    """记录稳定模型错误分类，不写异常正文、请求体或响应正文。"""
    event: dict[str, Any] = {
        "event": "MODEL_ERROR",
        "task_id": str(task_id),
    }
    if error_code:
        event["error_code"] = error_code
    if error_type:
        event["error_type"] = error_type
    _emit(event, run_id=run_id, level=logging.WARNING)


def log_tool_call(
    gateway: ModelGateway,
    task_id: TaskId,
    name: str,
    arguments: Mapping[str, Any],
    *,
    run_id: RunId | None = None,
) -> None:
    """记录工具名和有界、脱敏后的参数。"""
    text = _sanitize(
        gateway,
        task_id,
        json.dumps(_to_jsonable(arguments), ensure_ascii=False, default=str),
    )
    payload = _try_json(text)
    _emit(
        {
            "event": "TOOL_CALL",
            "task_id": str(task_id),
            "tool": name,
            "arguments": _bound(payload),
        },
        run_id=run_id,
    )


def log_tool_result(
    gateway: ModelGateway,
    task_id: TaskId,
    name: str,
    result: Any,
    *,
    run_id: RunId | None = None,
) -> None:
    """记录工具结果的有界摘要；不把大表或完整文件内容写入日志。"""
    encoded = json.dumps(_to_jsonable(result), ensure_ascii=False, default=str)
    safe_text = _sanitize(gateway, task_id, encoded)
    _emit(
        {
            "event": "TOOL_RESULT",
            "task_id": str(task_id),
            "tool": name,
            "result": _bound(_try_json(safe_text)),
        },
        run_id=run_id,
    )


def log_tool_error(
    task_id: TaskId,
    name: str,
    *,
    run_id: RunId | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
) -> None:
    """记录工具失败的稳定分类。"""
    event: dict[str, Any] = {
        "event": "TOOL_ERROR",
        "task_id": str(task_id),
        "tool": name,
    }
    if error_code:
        event["error_code"] = error_code
    if error_type:
        event["error_type"] = error_type
    _emit(event, run_id=run_id, level=logging.WARNING)


def _emit(event: dict[str, Any], *, run_id: RunId | None, level: int = logging.INFO) -> None:
    if run_id is not None:
        event["run_id"] = str(run_id)
    _LOGGER.log(level, "[agent] %s", json.dumps(event, ensure_ascii=False, sort_keys=True))


def _sanitize(gateway: ModelGateway, task_id: TaskId, text: str) -> str:
    """脱敏失败时 fail-closed；诊断日志不能反过来阻断 Agent。"""
    try:
        return gateway.sanitize_log(task_id, text).cloud_text
    except Exception:  # noqa: BLE001 — 日志故障不得改变业务执行
        return "<redacted>"


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _first_text(value: Any, *keys: str) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_jsonable(item) for item in value[:_MAX_ITEMS]]
    return value


def _bound(value: Any, *, depth: int = 0) -> Any:
    """限制深度、字符串和集合大小，避免诊断日志变成数据导出通道。"""
    if depth >= 4:
        return "<truncated>"
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "…"
    if isinstance(value, Mapping):
        items = list(value.items())[:_MAX_ITEMS]
        result = {str(key): _bound(item, depth=depth + 1) for key, item in items}
        if len(value) > _MAX_ITEMS:
            result["_truncated_items"] = len(value) - _MAX_ITEMS
        return result
    if isinstance(value, (tuple, list)):
        items = list(value)[:_MAX_ITEMS]
        result = [_bound(item, depth=depth + 1) for item in items]
        if len(value) > _MAX_ITEMS:
            result.append(f"<truncated {len(value) - _MAX_ITEMS} items>")
        return result
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _bound(str(value), depth=depth + 1)
