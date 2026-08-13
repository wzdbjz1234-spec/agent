"""ProjectCorpus 的本地 SQLite FTS5/BM25 索引。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dataharness.domain import FileId, FileVersionId, ProjectSnapshot

from .models import ExtractedDocument, SearchHit

INDEX_VERSION = "fts5-v1"


class CorpusIndex:
    """单项目全文索引；索引仅是可重建派生物，不是领域事实源。"""

    def __init__(self, path: Path, max_snippet_chars: int = 2000) -> None:
        self.path = path
        self.max_snippet_chars = max_snippet_chars

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            "CREATE TABLE IF NOT EXISTS index_metadata("
            "file_version_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, file_name TEXT NOT NULL, "
            "source_hash TEXT NOT NULL, media_type TEXT NOT NULL, extractor_version TEXT NOT NULL, "
            "index_version TEXT NOT NULL);"
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "file_version_id UNINDEXED, locator_json UNINDEXED, text, metadata_json UNINDEXED, "
            "tokenize='unicode61');"
        )
        return connection

    def replace(
        self,
        *,
        file_id: FileId,
        file_version_id: FileVersionId,
        file_name: str,
        document: ExtractedDocument,
    ) -> None:
        """事务化替换单个版本的全部片段，并固定源/提取器/索引版本。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM chunks WHERE file_version_id = ?", (str(file_version_id),)
            )
            connection.execute(
                "DELETE FROM index_metadata WHERE file_version_id = ?", (str(file_version_id),)
            )
            connection.execute(
                "INSERT INTO index_metadata VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(file_version_id),
                    str(file_id),
                    file_name,
                    document.source_hash,
                    document.media_type,
                    document.extractor_version,
                    INDEX_VERSION,
                ),
            )
            connection.executemany(
                "INSERT INTO chunks(file_version_id, locator_json, text, metadata_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        str(file_version_id),
                        json.dumps(chunk.locator, ensure_ascii=False, sort_keys=True),
                        chunk.text,
                        json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for chunk in document.chunks
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def search(
        self,
        snapshot: ProjectSnapshot,
        query: str,
        *,
        limit: int = 20,
        media_types: tuple[str, ...] | None = None,
    ) -> tuple[SearchHit, ...]:
        """仅检索 Snapshot 的 READY 成员，返回有界片段和真实来源定位。"""
        if limit <= 0 or limit > 100:
            raise ValueError("limit 必须在 1..100")
        terms = [term for term in query.replace('"', " ").split() if term]
        if not terms:
            return ()
        match = " OR ".join(f'"{term}"' for term in terms)
        ready_ids = [str(entry.file_version_id) for entry in snapshot.ready_entries()]
        if not ready_ids or not self.path.exists():
            return ()
        placeholders = ",".join("?" for _ in ready_ids)
        clauses = [f"chunks.file_version_id IN ({placeholders})", "chunks MATCH ?"]
        params: list[object] = [*ready_ids, match]
        if media_types:
            clauses.append(f"metadata.media_type IN ({','.join('?' for _ in media_types)})")
            params.extend(media_types)
        params.append(limit)
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT metadata.file_id, chunks.file_version_id, metadata.file_name, "
                "metadata.source_hash, metadata.media_type, chunks.text, chunks.locator_json, "
                "bm25(chunks) AS rank FROM chunks JOIN index_metadata AS metadata "
                "ON metadata.file_version_id = chunks.file_version_id WHERE "
                + " AND ".join(clauses)
                + " ORDER BY rank, chunks.rowid LIMIT ?",
                params,
            ).fetchall()
            return tuple(
                SearchHit(
                    project_id=snapshot.project_id,
                    file_id=FileId(row["file_id"]),
                    file_version_id=FileVersionId(row["file_version_id"]),
                    content_hash=row["source_hash"],
                    file_name=row["file_name"],
                    media_type=row["media_type"],
                    text=row["text"][: self.max_snippet_chars],
                    locator=json.loads(row["locator_json"]),
                    score=-float(row["rank"]),
                )
                for row in rows
            )
        finally:
            connection.close()
