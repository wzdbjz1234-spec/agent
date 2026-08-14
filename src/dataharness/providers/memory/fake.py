"""MemoryCapability 的确定性 fake Adapter。"""

from __future__ import annotations

from datetime import datetime

from dataharness.domain import ContentHash, ResourceRef, RunId, TaskId, compute_content_hash

from .sqlite import HistoryEntry, HistoryHit


class FakeHistoryStore:
    """使用内存列表模拟历史存储，不依赖 SQLite，供能力层契约测试使用。"""

    def __init__(self) -> None:
        self.entries: list[HistoryEntry] = []

    def add(
        self,
        *,
        task_id: TaskId,
        run_id: RunId,
        text: str,
        references: tuple[ResourceRef, ...] = (),
        created_at: datetime,
    ) -> HistoryEntry:
        """按与 SQLite Adapter 相同的幂等键保存历史。"""
        entry_id = str(compute_content_hash(f"{task_id}:{run_id}:{text}".encode()))
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        entry = HistoryEntry(
            id=entry_id,
            task_id=task_id,
            run_id=run_id,
            text=text,
            content_hash=ContentHash(compute_content_hash(text.encode())),
            references=references,
            created_at=created_at,
        )
        self.entries.append(entry)
        return entry

    def search(self, query: str, *, limit: int = 20) -> tuple[HistoryHit, ...]:
        """使用简单词项匹配模拟 FTS5 返回形状，保持测试不依赖私有 SQL。"""
        terms = tuple(query.casefold().split())
        if not terms:
            return ()
        hits = [
            HistoryHit(
                entry=entry,
                score=-float(sum(term in entry.text.casefold() for term in terms)),
            )
            for entry in self.entries
            if all(term in entry.text.casefold() for term in terms)
        ]
        return tuple(sorted(hits, key=lambda item: item.score)[:limit])
