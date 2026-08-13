"""ModelGateway 对正式与 fake cloud Adapter 的边界契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dataharness.domain import TaskId
from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy, SecretDetectedError
from dataharness.storage import PrivacyConnectionFactory


@dataclass
class FakeCloudModel:
    """确定性 Adapter：只回显云端视图，便于断言它从未获得明文 PII。"""

    calls: list[str] = field(default_factory=list)

    def complete(self, request: str) -> str:
        self.calls.append(request)
        return request


def _gateway(tmp_path: Path) -> tuple[ModelGateway, FakeCloudModel]:
    provider = FakeCloudModel()
    policy = PrivacyPolicy(
        PlaceholderStore(PrivacyConnectionFactory(tmp_path / "privacy", tmp_path / "runtime.db"))
    )
    return ModelGateway(provider, policy), provider


def test_model_gateway_contract_blocks_secret_and_only_passes_placeholder(tmp_path: Path) -> None:
    """任意 Adapter 都只能从 Gateway 获得脱敏请求，secret 根本没有 Provider 调用。"""
    gateway, provider = _gateway(tmp_path)
    response = gateway.complete(TaskId("task-a"), "email alice@example.test")
    assert provider.calls == ["email <PII:EMAIL:0001>"]
    assert response.cloud_text == "email <PII:EMAIL:0001>"

    with pytest.raises(SecretDetectedError):
        gateway.complete(TaskId("task-a"), "password=synthetic")
    assert len(provider.calls) == 1
