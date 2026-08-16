"""OpenAI-compatible Chat Completions Provider。

这个 Adapter 只接收 ``ModelGateway`` 已经脱敏的 JSON 请求，不保存请求内容，也不把
HTTP 客户端类型泄漏到 Agent 或编排层。Provider 将 PydanticAI 的窄消息/工具描述转换为
大多数 OpenAI-compatible 服务都支持的 Chat Completions 形状，再把工具调用转换回
Phase 08 ``FunctionModel`` 约定的稳定 JSON。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dataharness.config import ModelProviderConfig
from dataharness.privacy import ModelProviderError


def _part_kind(part: dict[str, Any]) -> str:
    """兼容 PydanticAI 不同版本使用的 ``part_kind``/``kind`` 字段名。"""
    return str(part.get("part_kind", part.get("kind", "")))


def _message_content(part: dict[str, Any]) -> str:
    """把文本内容规范化成 OpenAI API 接受的字符串，拒绝把内部对象原样上传。"""
    content = part.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 PydanticAI 消息投影成普通 Chat Completions 消息。"""
    result: list[dict[str, Any]] = []
    for message in messages:
        message_kind = str(message.get("kind", ""))
        for part in message.get("parts", []):
            if not isinstance(part, dict):
                continue
            kind = _part_kind(part)
            if kind in {"system-prompt", "system_prompt"}:
                result.append({"role": "system", "content": _message_content(part)})
            elif kind in {"user-prompt", "user_prompt"}:
                result.append({"role": "user", "content": _message_content(part)})
            elif kind in {"text", "response-text", "response_text"}:
                result.append({"role": "assistant", "content": _message_content(part)})
            elif kind in {"tool-call", "tool_call"}:
                args = part.get("args", {})
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False, sort_keys=True)
                result.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": str(part.get("tool_call_id", "gateway-tool-call")),
                                "type": "function",
                                "function": {
                                    "name": str(part.get("tool_name", "")),
                                    "arguments": args,
                                },
                            }
                        ],
                    }
                )
            elif kind in {"tool-return", "tool_return"}:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(part.get("tool_call_id", "gateway-tool-call")),
                        "content": _message_content(part),
                    }
                )
            elif message_kind == "request":
                # 未知请求部件不应被静默解释成自然语言；保留一个无敏感结构的文本
                # 片段，以便兼容未来的 PydanticAI 轻量消息部件。
                content = part.get("content")
                if isinstance(content, str) and content:
                    result.append({"role": "user", "content": content})
    return result


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 FunctionModel 的工具定义转换成 OpenAI function tool。"""
    result: list[dict[str, Any]] = []
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = tool.get("parameters_json_schema", {})
        if not isinstance(parameters, dict):
            parameters = {}
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description", "")),
                    "parameters": parameters,
                },
            }
        )
    return result


@dataclass(frozen=True, slots=True)
class OpenAICompatibleCloudModelProvider:
    """从本地 TOML 配置读取模型和 API Key 的同步 Provider。"""

    model: str
    base_url: str | None
    timeout_seconds: float
    api_key: str | None = field(repr=False)

    @classmethod
    def from_config(cls, config: ModelProviderConfig) -> OpenAICompatibleCloudModelProvider:
        """从 Settings 创建 Adapter；密钥只保存在进程内，不写入请求日志。"""
        return cls(
            model=config.model,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            api_key=config.api_key,
        )

    def _endpoint(self) -> str:
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    def complete(self, request: str) -> str:
        """发送一次已脱敏请求，并把所有失败映射为稳定、不回显正文的错误。"""
        if not self.api_key:
            raise ModelProviderError("模型 API Key 未配置", code="MODEL_API_KEY_MISSING")
        try:
            incoming = json.loads(request)
        except (TypeError, ValueError) as error:
            raise ModelProviderError("模型请求结构无效", code="MODEL_REQUEST_INVALID") from error
        if not isinstance(incoming, dict) or not isinstance(incoming.get("messages"), list):
            raise ModelProviderError("模型请求缺少消息", code="MODEL_REQUEST_INVALID")
        payload = {
            "model": self.model,
            "messages": _openai_messages(incoming["messages"]),
            "tools": _openai_tools(incoming.get("tools", [])),
            "temperature": 0,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        http_request = Request(
            self._endpoint(),
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as error:
            code = {
                401: "MODEL_AUTHENTICATION_FAILED",
                403: "MODEL_ACCESS_DENIED",
                408: "MODEL_TIMEOUT",
                429: "MODEL_RATE_LIMITED",
            }.get(error.code, "MODEL_UPSTREAM_ERROR")
            raise ModelProviderError(f"模型服务请求失败：{code}", code=code) from error
        except (TimeoutError, URLError, OSError) as error:
            raise ModelProviderError("模型服务不可达或超时", code="MODEL_TIMEOUT") from error
        if len(raw) > 8 * 1024 * 1024:
            raise ModelProviderError("模型响应超过大小上限", code="MODEL_RESPONSE_TOO_LARGE")
        try:
            response_payload = json.loads(raw)
            choice = response_payload["choices"][0]["message"]
        except (TypeError, KeyError, IndexError, ValueError) as error:
            raise ModelProviderError("模型响应结构无效", code="MODEL_RESPONSE_INVALID") from error
        tool_calls = choice.get("tool_calls") if isinstance(choice, dict) else None
        reasoning = choice.get("reasoning_content") if isinstance(choice, dict) else None
        reasoning_text = reasoning if isinstance(reasoning, str) and reasoning else None
        if isinstance(tool_calls, list) and tool_calls:
            call = tool_calls[0]
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(name, str) or not name:
                raise ModelProviderError("模型工具调用缺少名称", code="MODEL_RESPONSE_INVALID")
            arguments = function.get("arguments", "{}")
            try:
                args: Any = json.loads(arguments) if isinstance(arguments, str) else arguments
            except ValueError as error:
                raise ModelProviderError(
                    "模型工具参数不是有效 JSON", code="MODEL_RESPONSE_INVALID"
                ) from error
            result: dict[str, Any] = {
                "tool_call": {
                    "name": name,
                    "args": args,
                    "id": str(call.get("id", "gateway-tool-call")),
                }
            }
            if reasoning_text:
                result["reasoning"] = reasoning_text
            return json.dumps(result, ensure_ascii=False)
        content = choice.get("content") if isinstance(choice, dict) else None
        if not isinstance(content, str) or not content:
            raise ModelProviderError("模型响应没有文本内容", code="MODEL_RESPONSE_INVALID")
        if reasoning_text:
            return json.dumps(
                {"content": content, "reasoning": reasoning_text}, ensure_ascii=False
            )
        return content
