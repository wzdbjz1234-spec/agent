"""Phase 11 OpenAI-compatible Provider 的协议与错误分类测试。"""

from __future__ import annotations

import json

import pytest

from dataharness.config import ModelProviderConfig
from dataharness.privacy import ModelProviderError
from dataharness.providers.model import OpenAICompatibleCloudModelProvider, openai_compatible


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._data


def test_openai_compatible_provider_maps_tool_call_without_leaking_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data)
        return _Response(
            {
                "choices": [
                    {
                        "message": {
                            "reasoning_content": "先检索项目文件",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "search_project",
                                        "arguments": '{"query":"alpha"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(openai_compatible, "urlopen", fake_urlopen)
    provider = OpenAICompatibleCloudModelProvider.from_config(
        ModelProviderConfig(
            model="synthetic-model",
            base_url="http://model.test/v1",
            api_key="synthetic-key",
            timeout_seconds=3,
        )
    )
    result = provider.complete(
        json.dumps(
            {
                "messages": [
                    {
                        "kind": "request",
                        "parts": [{"part_kind": "user-prompt", "content": "alpha"}],
                    }
                ],
                "tools": [
                    {
                        "name": "search_project",
                        "description": "search",
                        "parameters_json_schema": {"type": "object"},
                    }
                ],
            }
        )
    )
    payload = json.loads(result)
    assert payload["tool_call"]["name"] == "search_project"
    assert payload["reasoning"] == "先检索项目文件"
    assert captured["url"] == "http://model.test/v1/chat/completions"
    assert captured["auth"] == "Bearer synthetic-key"
    assert captured["body"]["model"] == "synthetic-model"  # type: ignore[index]


def test_openai_compatible_provider_reports_missing_key_without_request() -> None:
    provider = OpenAICompatibleCloudModelProvider(
        model="model",
        base_url="http://model.test/v1",
        timeout_seconds=1,
        api_key=None,
    )
    with pytest.raises(ModelProviderError) as error:
        provider.complete('{"messages": []}')
    assert error.value.code == "MODEL_API_KEY_MISSING"


def test_deepseek_provider_uses_openai_compatible_adapter() -> None:
    provider = OpenAICompatibleCloudModelProvider.from_config(
        ModelProviderConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="synthetic-key",
        )
    )
    assert provider.model == "deepseek-v4-flash"
