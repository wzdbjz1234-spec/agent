"""把 ModelGateway 适配成 PydanticAI FunctionModel。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dataharness.domain import RunId
from dataharness.privacy import ModelGateway, ModelProviderError

from .diagnostics import log_model_error, log_model_output


def _render_request(messages: list[ModelMessage], info: AgentInfo) -> str:
    """把 PydanticAI 消息和窄工具定义渲染成稳定的网关请求。"""
    messages_json = ModelMessagesTypeAdapter.dump_json(messages, ensure_ascii=False).decode("utf-8")
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters_json_schema": tool.parameters_json_schema,
            "kind": tool.kind,
            "toolset_id": tool.toolset_id,
        }
        for tool in info.function_tools
    ]
    return json.dumps(
        {
            "messages": json.loads(messages_json),
            "tools": tools,
            "output": "当不再需要工具时，直接输出面向用户的自然语言，不要包装成 JSON。",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _response_parts(text: str) -> list[TextPart | ToolCallPart]:
    """解析一个或多个工具调用 JSON；普通模型文本直接作为自然语言回答。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [TextPart(content=text)]
    if not isinstance(payload, dict) or "tool_call" not in payload:
        # 兼容旧 Provider/测试返回的 {status, answer} 载荷；新 Agent 不会要求或
        # 生成这种格式，兼容分支仅把其中的用户可见文本还原为普通 TextPart。
        if isinstance(payload, dict) and isinstance(payload.get("answer"), str):
            return [TextPart(content=payload["answer"])]
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            return [TextPart(content=payload["content"])]
        calls = payload.get("tool_calls") if isinstance(payload, dict) else None
        if isinstance(calls, list):
            parts: list[TextPart | ToolCallPart] = []
            for call in calls:
                part = _tool_call_part(call)
                if part is None:
                    return [TextPart(content=text)]
                parts.append(part)
            return parts or [TextPart(content=text)]
        return [TextPart(content=text)]
    tool_call = payload["tool_call"]
    part = _tool_call_part(tool_call)
    return [part] if part is not None else [TextPart(content=text)]


def _tool_call_part(value: Any) -> ToolCallPart | None:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    args: Any = value.get("args", {})
    if not isinstance(args, (dict, str)) and args is not None:
        return None
    return ToolCallPart(
        tool_name=value["name"],
        args=args,
        tool_call_id=str(value.get("id", "gateway-tool-call")),
    )


def gateway_function_model(
    gateway: ModelGateway, scope_id: str, run_id: RunId | None = None
) -> FunctionModel:
    """创建所有请求都经 ModelGateway 的异步 FunctionModel。"""

    async def request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _render_request(messages, info)
        try:
            prepared = await asyncio.to_thread(gateway.complete, scope_id, prompt)
        except ModelProviderError as error:
            log_model_error(scope_id, run_id=run_id, error_code=error.code)
            raise
        except Exception as error:  # noqa: BLE001 — 只记录异常类型，不记录正文
            log_model_error(scope_id, run_id=run_id, error_type=type(error).__name__)
            raise
        log_model_output(gateway, scope_id, prepared.cloud_text, run_id=run_id)
        return ModelResponse(parts=_response_parts(prepared.cloud_text), model_name="gateway")

    function: Any = request
    return FunctionModel(function=function)
