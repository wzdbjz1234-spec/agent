"""ModelGateway 使用的隐私策略与无明文审计元数据。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dataharness.domain import TaskId, compute_content_hash

from .detector import PIIDetector, SecretDetector, SensitiveMatch
from .placeholders import PlaceholderStore, ScanCacheEntry


class SecretDetectedError(ValueError):
    """云端请求命中凭据时的 fail-closed 错误；异常消息绝不回显凭据。"""

    def __init__(self, matches: tuple[SensitiveMatch, ...]) -> None:
        self.matches = matches
        kinds = ", ".join(sorted({item.kind for item in matches}))
        super().__init__(f"请求包含被阻断的凭据类型：{kinds}")


class BoundaryKind(StrEnum):
    """需要统一再扫描的模型边界内容类别。"""

    RESPONSE = "response"
    TOOL_RESULT = "tool_result"
    EXCEPTION = "exception"
    COMPACTION = "compaction"
    LOG = "log"
    TRACE = "trace"


@dataclass(frozen=True, slots=True)
class PrivacyAudit:
    """可写入日志/trace 的脱敏审计摘要。"""

    content_hash: str
    pii_kinds: tuple[str, ...]
    pii_count: int
    secret_kinds: tuple[str, ...]
    secret_count: int


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    """已可安全交给云模型 Adapter 的请求与其审计摘要。"""

    cloud_text: str
    audit: PrivacyAudit


class PrivacyPolicy:
    """所有模型边界的统一本地保护策略。

    请求中的 secret 直接阻断；PII 只在云端视图替换。响应、异常、工具结果、压缩内容与
    可观测性文本采用同一再扫描路径，避免任何旁路把原始 PII 或凭据写入日志/trace。
    """

    def __init__(
        self,
        store: PlaceholderStore,
        *,
        secret_detector: SecretDetector | None = None,
        pii_detector: PIIDetector | None = None,
    ) -> None:
        self._store = store
        self._secrets = secret_detector or SecretDetector()
        self._pii = pii_detector or PIIDetector()

    def _scan(self, task_id: TaskId, text: str) -> ScanCacheEntry:
        content_hash = str(compute_content_hash(text.encode("utf-8")))
        cached = self._store.get_cached_scan(task_id, content_hash)
        if cached is not None:
            return cached
        entry = ScanCacheEntry(secrets=self._secrets.scan(text), pii=self._pii.scan(text))
        self._store.cache_scan(task_id, content_hash, entry)
        return entry

    @staticmethod
    def _audit(text: str, entry: ScanCacheEntry) -> PrivacyAudit:
        return PrivacyAudit(
            content_hash=str(compute_content_hash(text.encode("utf-8"))),
            pii_kinds=tuple(sorted({item.kind for item in entry.pii})),
            pii_count=len(entry.pii),
            secret_kinds=tuple(sorted({item.kind for item in entry.secrets})),
            secret_count=len(entry.secrets),
        )

    def prepare_request(self, task_id: TaskId, text: str) -> PreparedRequest:
        """扫描新增请求；凭据 fail closed，PII 转为该 Task 专属的云端视图。"""
        entry = self._scan(task_id, text)
        if entry.secrets:
            raise SecretDetectedError(entry.secrets)
        return PreparedRequest(self._store.mask(task_id, text, entry.pii), self._audit(text, entry))

    def sanitize_boundary_text(
        self, task_id: TaskId, text: str, kind: BoundaryKind
    ) -> PreparedRequest:
        """对 Provider 返回内容及所有辅助文本做再扫描并返回可记录的脱敏版本。"""
        del kind  # 类别属于调用方审计维度；所有边界共享同一不可绕过的规则。
        entry = self._scan(task_id, text)
        redacted = text
        for match in sorted(entry.secrets, key=lambda item: item.start, reverse=True):
            redacted = redacted[: match.start] + f"<SECRET:{match.kind}>" + redacted[match.end :]
        # Secret 替换会改变位置，因此对替换后的文本重新检测 PII 后再建立映射。
        pii_after_secret_redaction = self._pii.scan(redacted)
        return PreparedRequest(
            self._store.mask(task_id, redacted, pii_after_secret_redaction),
            self._audit(text, entry),
        )

    def restore_tool_input(
        self, task_id: TaskId, text: str, *, allowed_kinds: tuple[str, ...]
    ) -> str:
        """在进入 Sandbox 前按工具声明的 PII 类型白名单受控恢复。"""
        return self._store.restore(task_id, text, allowed_kinds=allowed_kinds)
