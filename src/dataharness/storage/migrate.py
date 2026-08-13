"""有序、事务化的 Runtime SQLite migration runner。"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .errors import MigrationError

_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    """单个不可变迁移脚本。"""

    version: int
    name: str
    sql: str


def discover_migrations(directory: Path | None = None) -> tuple[Migration, ...]:
    """按文件名前缀发现迁移，并拒绝重复版本或版本空洞。"""
    root = directory or Path(str(files("dataharness.storage.migrations")))
    discovered: list[Migration] = []
    for path in sorted(root.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"非法迁移文件名：{path.name}")
        discovered.append(
            Migration(int(match.group("version")), match.group("name"), path.read_text("utf-8"))
        )

    versions = [item.version for item in discovered]
    if len(versions) != len(set(versions)):
        raise MigrationError("迁移版本重复")
    if versions and versions != list(range(1, versions[-1] + 1)):
        raise MigrationError(f"迁移版本必须从 1 连续递增，实际为 {versions}")
    return tuple(discovered)


def _statements(script: str) -> Iterable[str]:
    """按 SQLite 自身的 complete_statement 规则切分，正确保留 trigger 内分号。"""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                yield statement
    if buffer.strip():
        raise MigrationError("迁移 SQL 末尾存在不完整语句")


def current_schema_version(connection: sqlite3.Connection) -> int:
    """返回已提交的 schema 版本；未初始化数据库返回 0。"""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def migrate(connection: sqlite3.Connection, migrations: tuple[Migration, ...] | None = None) -> int:
    """将连接升级到最新版本，每个版本独立事务，失败版本完整回滚。"""
    ordered = migrations if migrations is not None else discover_migrations()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    applied_rows = connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    applied = {int(row[0]): str(row[1]) for row in applied_rows}
    known = {item.version: item.name for item in ordered}
    for version, name in applied.items():
        if known.get(version) != name:
            raise MigrationError(f"已应用迁移 {version}:{name} 与当前序列不一致")

    for item in ordered:
        if item.version in applied:
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _statements(item.sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (item.version, item.name),
            )
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise MigrationError(f"迁移 {item.version}:{item.name} 失败") from error
    return current_schema_version(connection)
