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

from dataharness.domain import TaskId
from dataharness.privacy import ModelGateway


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
            "output": "当不再需要工具时，输出符合结构化输出定义的 JSON。",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _response_part(text: str) -> TextPart | ToolCallPart:
    """解析 Fake/真实 Provider 约定的工具调用 JSON，其余内容交给结构化输出校验。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return TextPart(content=text)
    if not isinstance(payload, dict) or "tool_call" not in payload:
        return TextPart(content=text)
    tool_call = payload["tool_call"]
    if not isinstance(tool_call, dict) or not isinstance(tool_call.get("name"), str):
        return TextPart(content=text)
    args: Any = tool_call.get("args", {})
    if not isinstance(args, (dict, str)) and args is not None:
        return TextPart(content=text)
    return ToolCallPart(
        tool_name=tool_call["name"],
        args=args,
        tool_call_id=str(tool_call.get("id", "gateway-tool-call")),
    )


def gateway_function_model(gateway: ModelGateway, task_id: TaskId) -> FunctionModel:
    """创建所有请求都经 ModelGateway 的异步 FunctionModel。"""

    async def request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _render_request(messages, info)
        prepared = await asyncio.to_thread(gateway.complete, task_id, prompt)
        return ModelResponse(parts=[_response_part(prepared.cloud_text)], model_name="gateway")

    function: Any = request
    return FunctionModel(function=function)
