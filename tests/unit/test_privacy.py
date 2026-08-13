"""隐私规则、Task 占位与 ModelGateway 的确定性单元测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dataharness.domain import TaskId
from dataharness.privacy import (
    CustomPIIRule,
    ModelGateway,
    ModelProviderError,
    PIIDetector,
    PlaceholderRestoreError,
    PlaceholderStore,
    PrivacyPolicy,
    SecretDetectedError,
    SecretDetector,
)
from dataharness.storage import PrivacyConnectionFactory


def _policy(tmp_path: Path) -> PrivacyPolicy:
    """创建只位于 pytest 临时目录内的单 Task Privacy DB 策略。"""
    return PrivacyPolicy(
        PlaceholderStore(PrivacyConnectionFactory(tmp_path / "privacy", tmp_path / "runtime.db"))
    )


@dataclass
class RecordingProvider:
    """不联网的 fake cloud Adapter，用于证明 Gateway 是唯一可到达路径。"""

    response: str = "ok"
    failure: Exception | None = None
    requests: list[str] = field(default_factory=list)

    def complete(self, request: str) -> str:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.response


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("password=synthetic-only-value", "PASSWORD"),
        ("api_key: synthetic-token-value", "API_TOKEN"),
        ("Cookie: session=synthetic-only", "COOKIE"),
        ("postgresql://user:synthetic@db.example/test", "CONNECTION_STRING"),
        (
            "-----BEGIN PRIVATE KEY-----\nsynthetic-only\n-----END PRIVATE KEY-----",
            "PRIVATE_KEY",
        ),
    ],
)
def test_secret_detector_covers_v1_blocking_categories(text: str, kind: str) -> None:
    """五类明确凭据均会命中本地规则，测试数据不含真实凭据。"""
    assert kind in {item.kind for item in SecretDetector().scan(text)}


def test_pii_detector_covers_default_and_explicit_rules() -> None:
    """默认 PII 与用户显式规则均成为类型化占位候选。"""
    detector = PIIDetector((CustomPIIRule("EMPLOYEE_CODE", r"EMP-\d{4}"),))
    text = (
        "mail alice@example.test, phone 138 0013 8000, card 4111 1111 1111 1111, "
        "id 11010519491231002X, EMP-0007"
    )
    kinds = {item.kind for item in detector.scan(text)}
    assert {"EMAIL", "PHONE", "BANK_CARD", "NATIONAL_ID", "EMPLOYEE_CODE"} <= kinds


def test_gateway_blocks_secret_before_fake_cloud_provider(tmp_path: Path) -> None:
    """凭据请求 fail closed，fake cloud 一次也不能收到原始文本或替代文本。"""
    provider = RecordingProvider()
    gateway = ModelGateway(provider, _policy(tmp_path))
    with pytest.raises(SecretDetectedError):
        gateway.complete(TaskId("task-a"), "please inspect password=synthetic-only-value")
    assert provider.requests == []


def test_gateway_masks_pii_without_mutating_caller_text_and_rescans_response(
    tmp_path: Path,
) -> None:
    """云端视图、模型回复和调用方原文彼此隔离。"""
    original = "contact alice@example.test at 13800138000"
    provider = RecordingProvider(response="reply to bob@example.test")
    gateway = ModelGateway(provider, _policy(tmp_path))

    response = gateway.complete(TaskId("task-a"), original)

    assert original == "contact alice@example.test at 13800138000"
    assert "alice@example.test" not in provider.requests[0]
    assert "13800138000" not in provider.requests[0]
    assert "<PII:EMAIL:0001>" in provider.requests[0]
    assert response.cloud_text == "reply to <PII:EMAIL:0002>"
    assert response.audit.pii_count == 1


def test_placeholder_is_stable_per_task_and_cannot_be_linked_across_tasks(tmp_path: Path) -> None:
    """同 Task 复用映射，另一 Task 从独立 Privacy DB 重新编号。"""
    policy = _policy(tmp_path)
    first = policy.prepare_request(TaskId("task-a"), "alice@example.test")
    second = policy.prepare_request(TaskId("task-a"), "ALICE@example.test")
    other = policy.prepare_request(TaskId("task-b"), "alice@example.test")

    assert first.cloud_text == second.cloud_text == "<PII:EMAIL:0001>"
    assert other.cloud_text == "<PII:EMAIL:0001>"
    assert (
        policy.restore_tool_input(TaskId("task-a"), first.cloud_text, allowed_kinds=("EMAIL",))
        == "alice@example.test"
    )
    with pytest.raises(PlaceholderRestoreError):
        policy.restore_tool_input(TaskId("task-b"), "<PII:EMAIL:0002>", allowed_kinds=("EMAIL",))


def test_restore_requires_registered_placeholder_and_declared_type(tmp_path: Path) -> None:
    """模型伪造占位符或工具声明错误类型时，恢复必须被拒绝。"""
    policy = _policy(tmp_path)
    prepared = policy.prepare_request(TaskId("task-a"), "alice@example.test")
    with pytest.raises(PlaceholderRestoreError):
        policy.restore_tool_input(TaskId("task-a"), prepared.cloud_text, allowed_kinds=("PHONE",))
    with pytest.raises(PlaceholderRestoreError):
        policy.restore_tool_input(TaskId("task-a"), "<PII:EMAIL:9999>", allowed_kinds=("EMAIL",))


def test_all_auxiliary_boundaries_are_rescanned_and_provider_error_is_safe(tmp_path: Path) -> None:
    """回复、工具、压缩、日志、trace 与异常均不能泄漏 PII/凭据。"""
    gateway = ModelGateway(
        RecordingProvider(failure=RuntimeError("failed for alice@example.test password=synthetic")),
        _policy(tmp_path),
    )
    task_id = TaskId("task-a")
    with pytest.raises(ModelProviderError) as error:
        gateway.complete(task_id, "ordinary request")
    assert "alice@example.test" not in str(error.value)
    assert "synthetic" not in str(error.value)

    for sanitize in (
        gateway.sanitize_tool_result,
        gateway.sanitize_compaction,
        gateway.sanitize_log,
        gateway.sanitize_trace,
    ):
        safe = sanitize(task_id, "alice@example.test password=synthetic")
        assert "alice@example.test" not in safe.cloud_text
        assert "synthetic" not in safe.cloud_text


def test_detection_result_is_cached_by_content_hash_in_privacy_db(tmp_path: Path) -> None:
    """重复内容读取同一 Task 的缓存，不在 Runtime DB 或 Workspace 建立副本。"""
    policy = _policy(tmp_path)
    task_id = TaskId("task-a")
    policy.prepare_request(task_id, "alice@example.test")
    policy.prepare_request(task_id, "alice@example.test")
    database = tmp_path / "privacy" / "task-a.db"
    assert database.is_file()
    import sqlite3

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM scan_cache").fetchone()[0] == 1
