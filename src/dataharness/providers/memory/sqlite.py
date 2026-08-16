"""独立于项目索引的 SQLite FTS5/BM25 对话历史 Provider。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from dataharness.domain import (
    ContentHash,
    ProjectId,
    ResourceRef,
    RunId,
    SessionId,
    TaskId,
    compute_content_hash,
)


class HistoryEntry(BaseModel):
    """一条可检索的脱敏对话历史；资源引用只保存稳定 ID 与哈希。"""

    model_config = ConfigDict(frozen=True)

    id: str
    task_id: TaskId
    run_id: RunId
    text: str = Field(min_length=1)
    content_hash: ContentHash
    references: tuple[ResourceRef, ...] = ()
    created_at: datetime
    project_id: ProjectId | None = None
    session_id: SessionId | None = None


class HistoryHit(BaseModel):
    """FTS5 BM25 检索命中的历史摘要。"""

    model_config = ConfigDict(frozen=True)

    entry: HistoryEntry
    score: float


class HistoryStore(Protocol):
    """历史存储 Adapter 的最小协议，便于用 fake 验证能力层行为。"""

    def add(
        self,
        *,
        task_id: TaskId,
        run_id: RunId,
        project_id: ProjectId | None = None,
        session_id: SessionId | None = None,
        text: str,
        references: tuple[ResourceRef, ...] = (),
        created_at: datetime,
    ) -> HistoryEntry: ...

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        project_id: ProjectId | None = None,
        session_id: SessionId | None = None,
    ) -> tuple[HistoryHit, ...]: ...


class SqliteHistoryStore:
    """使用独立 SQLite 文件保存可选历史，不与 ProjectCorpus 共用表或索引。"""

    def __init__(self, path: Path) -> None:
        self._path = path.absolute()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_entries (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    project_id TEXT,
                    session_id TEXT,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    references_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS history_fts
                USING fts5(entry_id UNINDEXED, content, tokenize='unicode61');
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(history_entries)").fetchall()
            }
            if "project_id" not in columns:
                connection.execute("ALTER TABLE history_entries ADD COLUMN project_id TEXT")
            if "session_id" not in columns:
                connection.execute("ALTER TABLE history_entries ADD COLUMN session_id TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> HistoryEntry:
        references = tuple(
            ResourceRef.model_validate(item) for item in json.loads(row["references_json"])
        )
        return HistoryEntry(
            id=row["id"],
            task_id=TaskId(row["task_id"]),
            run_id=RunId(row["run_id"]),
            project_id=ProjectId(row["project_id"]) if row["project_id"] else None,
            session_id=SessionId(row["session_id"]) if row["session_id"] else None,
            text=row["text"],
            content_hash=ContentHash(row["content_hash"]),
            references=references,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def add(
        self,
        *,
        task_id: TaskId,
        run_id: RunId,
        project_id: ProjectId | None = None,
        session_id: SessionId | None = None,
        text: str,
        references: tuple[ResourceRef, ...] = (),
        created_at: datetime,
    ) -> HistoryEntry:
        """幂等写入已经脱敏的历史文本，并同步维护 FTS5 文档。"""
        if not text.strip():
            raise ValueError("历史文本不能为空")
        entry_id = str(compute_content_hash(f"{task_id}:{run_id}:{text}".encode()))
        entry = HistoryEntry(
            id=entry_id,
            task_id=task_id,
            run_id=run_id,
            project_id=project_id,
            session_id=session_id,
            text=text,
            content_hash=compute_content_hash(text.encode("utf-8")),
            references=references,
            created_at=created_at,
        )
        payload = json.dumps(
            [item.model_dump(mode="json") for item in references],
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO history_entries "
                "(id, task_id, run_id, project_id, session_id, text, content_hash, "
                "references_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    str(task_id),
                    str(run_id),
                    str(project_id) if project_id else None,
                    str(session_id) if session_id else None,
                    entry.text,
                    str(entry.content_hash),
                    payload,
                    entry.created_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO history_fts(entry_id, content) VALUES (?, ?)",
                (entry.id, entry.text),
            )
        return entry

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        project_id: ProjectId | None = None,
        session_id: SessionId | None = None,
    ) -> tuple[HistoryHit, ...]:
        """按 FTS5 BM25 排序检索历史；查询语法错误时按普通短语重试。"""
        if not query.strip():
            return ()
        if not 0 < limit <= 100:
            raise ValueError("历史检索 limit 必须在 1 到 100 之间")
        match_query = query.strip()
        scope_sql = ""
        scope_params: list[object] = []
        if project_id is not None:
            scope_sql += " AND e.project_id = ?"
            scope_params.append(str(project_id))
        if session_id is not None:
            scope_sql += " AND e.session_id = ?"
            scope_params.append(str(session_id))
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    "SELECT e.*, bm25(history_fts) AS score "
                    "FROM history_fts JOIN history_entries e ON e.id = history_fts.entry_id "
                    "WHERE history_fts MATCH ?"
                    + scope_sql
                    + " ORDER BY score, e.created_at DESC LIMIT ?",
                    (match_query, *scope_params, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                phrase = '"' + match_query.replace('"', '""') + '"'
                rows = connection.execute(
                    "SELECT e.*, bm25(history_fts) AS score "
                    "FROM history_fts JOIN history_entries e ON e.id = history_fts.entry_id "
                    "WHERE history_fts MATCH ?"
                    + scope_sql
                    + " ORDER BY score, e.created_at DESC LIMIT ?",
                    (phrase, *scope_params, limit),
                ).fetchall()
        return tuple(
            HistoryHit(entry=self._entry_from_row(row), score=float(row["score"])) for row in rows
        )
