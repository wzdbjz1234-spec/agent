"""Runtime 与 Privacy SQLite 的物理隔离连接工厂。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dataharness.domain import TaskId

from .migrate import migrate


def _configure(connection: sqlite3.Connection, *, wal: bool) -> None:
    """为每个连接启用一致的完整性、等待与行访问策略。"""
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if wal:
        # journal_mode 必须在事务外设置；文件数据库返回 WAL，内存数据库会返回 memory。
        connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")


@dataclass(frozen=True, slots=True)
class RuntimeConnectionFactory:
    """只创建 Runtime DB 连接，并在首次连接时运行有序迁移。"""

    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())

    def connect(self, *, apply_migrations: bool = True) -> sqlite3.Connection:
        """创建调用方独占的连接；连接生命周期由调用方管理。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        _configure(connection, wal=True)
        if apply_migrations:
            migrate(connection)
        return connection


@dataclass(frozen=True, slots=True)
class PrivacyConnectionFactory:
    """为每个 Task 派生独立 Privacy DB；本阶段不定义其业务 schema。"""

    root: Path
    runtime_db_path: Path

    def __post_init__(self) -> None:
        resolved_root = self.root.resolve()
        runtime = self.runtime_db_path.resolve()
        if resolved_root == runtime or runtime.is_relative_to(resolved_root):
            raise ValueError("Privacy 根目录不得包含 Runtime DB 路径")
        object.__setattr__(self, "root", resolved_root)
        object.__setattr__(self, "runtime_db_path", runtime)

    def path_for(self, task_id: TaskId) -> Path:
        """返回 Task 专属路径；ID 只作为单个文件名且禁止路径分隔符。"""
        value = str(task_id)
        if not value or Path(value).name != value or "/" in value or "\\" in value:
            raise ValueError("task_id 不能包含路径分隔符")
        return self.root / f"{value}.db"

    def connect(self, task_id: TaskId) -> sqlite3.Connection:
        """创建 Task Privacy SQLite 连接，与 Runtime 连接和迁移序列完全分离。"""
        path = self.path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        _configure(connection, wal=True)
        return connection
