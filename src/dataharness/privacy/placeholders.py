"""按 Agent scope 隔离的 PII 占位映射与检测结果缓存。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from dataharness.domain import compute_content_hash
from dataharness.storage import PrivacyConnectionFactory

from .detector import SensitiveMatch

_PLACEHOLDER = re.compile(r"<PII:([A-Z][A-Z0-9_]{0,31}):(\d{4,})>")


class PlaceholderRestoreError(ValueError):
    """工具输入包含不存在、跨 scope 或类型不匹配的占位符。"""


@dataclass(frozen=True, slots=True)
class ScanCacheEntry:
    """按内容哈希持久化的无明文扫描结果。"""

    secrets: tuple[SensitiveMatch, ...]
    pii: tuple[SensitiveMatch, ...]


class PlaceholderStore:
    """每个 Conversation/Analysis scope 一份 Privacy SQLite 的映射仓库。

    映射明文只存于受限的 Privacy DB；Runtime SQLite、Workspace、Sandbox、日志和审计对象
    都不会接触它。扫描缓存只保存内容哈希、种类和字符位置，避免为缓存复制敏感原文。
    """

    def __init__(self, connections: PrivacyConnectionFactory) -> None:
        self._connections = connections

    def _connect(self, scope_id: str) -> sqlite3.Connection:
        connection = self._connections.connect(scope_id)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pii_mappings (
                placeholder TEXT PRIMARY KEY,
                pii_kind TEXT NOT NULL,
                normalized_hash TEXT NOT NULL,
                original_value TEXT NOT NULL,
                UNIQUE(pii_kind, normalized_hash)
            );
            CREATE TABLE IF NOT EXISTS scan_cache (
                content_hash TEXT PRIMARY KEY,
                secret_matches_json TEXT NOT NULL,
                pii_matches_json TEXT NOT NULL
            );
            """
        )
        return connection

    @staticmethod
    def _encode(matches: tuple[SensitiveMatch, ...]) -> str:
        return json.dumps(
            [{"kind": item.kind, "start": item.start, "end": item.end} for item in matches],
            separators=(",", ":"),
        )

    @staticmethod
    def _decode(value: str) -> tuple[SensitiveMatch, ...]:
        return tuple(SensitiveMatch(**item) for item in json.loads(value))

    def get_cached_scan(self, scope_id: str, content_hash: str) -> ScanCacheEntry | None:
        """取得同一内容的既有检测结果；调用方仍以当前文本切片，不读取原文缓存。"""
        connection = self._connect(scope_id)
        try:
            row = connection.execute(
                "SELECT secret_matches_json, pii_matches_json "
                "FROM scan_cache WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if row is None:
                return None
            return ScanCacheEntry(self._decode(row[0]), self._decode(row[1]))
        finally:
            connection.close()

    def cache_scan(self, scope_id: str, content_hash: str, entry: ScanCacheEntry) -> None:
        """幂等保存无明文扫描缓存。"""
        connection = self._connect(scope_id)
        try:
            connection.execute(
                "INSERT OR IGNORE INTO scan_cache("
                "content_hash, secret_matches_json, pii_matches_json) VALUES (?, ?, ?)",
                (content_hash, self._encode(entry.secrets), self._encode(entry.pii)),
            )
        finally:
            connection.close()

    @staticmethod
    def _normalized(kind: str, value: str) -> str:
        """按类型稳定规范化，确保格式不同的同一邮箱/号码不会获得多个占位符。"""
        if kind == "EMAIL":
            return value.casefold()
        if kind in {"PHONE", "BANK_CARD", "NATIONAL_ID"}:
            return "".join(char for char in value.upper() if char.isalnum())
        return value

    def _placeholder_for(self, connection: sqlite3.Connection, kind: str, value: str) -> str:
        normalized_hash = str(compute_content_hash(self._normalized(kind, value).encode("utf-8")))
        existing = connection.execute(
            "SELECT placeholder FROM pii_mappings WHERE pii_kind = ? AND normalized_hash = ?",
            (kind, normalized_hash),
        ).fetchone()
        if existing is not None:
            return str(existing[0])
        # 写锁使同一 Task 的并发请求按顺序分配序号，且 UNIQUE 约束确保相同规范化值复用。
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT placeholder FROM pii_mappings WHERE pii_kind = ? AND normalized_hash = ?",
                (kind, normalized_hash),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return str(existing[0])
            sequence = (
                connection.execute(
                    "SELECT COUNT(*) FROM pii_mappings WHERE pii_kind = ?", (kind,)
                ).fetchone()[0]
                + 1
            )
            placeholder = f"<PII:{kind}:{sequence:04d}>"
            connection.execute(
                "INSERT INTO pii_mappings(placeholder, pii_kind, normalized_hash, original_value) "
                "VALUES (?, ?, ?, ?)",
                (placeholder, kind, normalized_hash, value),
            )
            connection.commit()
            return placeholder
        except Exception:
            connection.rollback()
            raise

    def mask(self, scope_id: str, text: str, matches: Iterable[SensitiveMatch]) -> str:
        """将 PII 替换为当前 scope 内稳定占位符；原始传入字符串保持不变。"""
        selected = tuple(matches)
        if not selected:
            return text
        connection = self._connect(scope_id)
        try:
            result = text
            for match in sorted(selected, key=lambda item: item.start, reverse=True):
                value = text[match.start : match.end]
                placeholder = self._placeholder_for(connection, match.kind, value)
                result = result[: match.start] + placeholder + result[match.end :]
            return result
        finally:
            connection.close()

    def restore(self, scope_id: str, text: str, *, allowed_kinds: Iterable[str]) -> str:
        """仅恢复当前 Task 已登记且显式允许类型的占位符，拒绝模型伪造的标记。"""
        allowed = frozenset(allowed_kinds)
        connection = self._connect(scope_id)
        try:

            def replace(match: re.Match[str]) -> str:
                kind, _ = match.groups()
                placeholder = match.group(0)
                row = connection.execute(
                    "SELECT pii_kind, original_value FROM pii_mappings WHERE placeholder = ?",
                    (placeholder,),
                ).fetchone()
                if row is None:
                    raise PlaceholderRestoreError("占位符未在当前 Task 的 Privacy DB 中登记")
                if row[0] != kind or kind not in allowed:
                    raise PlaceholderRestoreError("占位符类型不符合当前工具输入的恢复白名单")
                return str(row[1])

            return _PLACEHOLDER.sub(replace, text)
        finally:
            connection.close()
