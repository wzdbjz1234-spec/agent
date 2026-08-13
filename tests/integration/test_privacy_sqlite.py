"""Privacy SQLite 与 Runtime/Workspace 的物理隔离集成测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dataharness.domain import TaskId
from dataharness.privacy import PlaceholderStore, PrivacyPolicy
from dataharness.storage import PrivacyConnectionFactory, RuntimeConnectionFactory


def test_pii_mapping_and_scan_cache_never_enter_runtime_or_workspace(tmp_path: Path) -> None:
    """真实 SQLite 文件证明映射只写入每 Task Privacy DB，其他持久化边界保持无痕。"""
    runtime_path = tmp_path / "runtime" / "runtime.db"
    RuntimeConnectionFactory(runtime_path).connect().close()
    privacy_path = tmp_path / "privacy"
    policy = PrivacyPolicy(PlaceholderStore(PrivacyConnectionFactory(privacy_path, runtime_path)))

    policy.prepare_request(TaskId("task-1"), "alice@example.test")

    with sqlite3.connect(runtime_path) as runtime:
        tables = {
            item[0]
            for item in runtime.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    with sqlite3.connect(privacy_path / "task-1.db") as privacy:
        mappings = privacy.execute("SELECT pii_kind, original_value FROM pii_mappings").fetchall()
        cache_entries = privacy.execute("SELECT COUNT(*) FROM scan_cache").fetchone()[0]
    assert "pii_mappings" not in tables
    assert "scan_cache" not in tables
    assert mappings == [("EMAIL", "alice@example.test")]
    assert cache_entries == 1
    assert not (tmp_path / "projects").exists()
