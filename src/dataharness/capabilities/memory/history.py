"""对话历史能力：只把脱敏历史交给独立 FTS5 Provider。"""

from __future__ import annotations

from datetime import datetime

from dataharness.domain import ResourceRef, RunId, TaskId
from dataharness.privacy import ModelGateway
from dataharness.providers.memory import HistoryEntry, HistoryHit, HistoryStore


class MemoryCapability:
    """可选历史检索能力，不复制项目文件索引，也不生成向量记忆。"""

    def __init__(self, store: HistoryStore, gateway: ModelGateway) -> None:
        self._store = store
        self._gateway = gateway

    def remember(
        self,
        *,
        task_id: TaskId,
        run_id: RunId,
        text: str,
        references: tuple[ResourceRef, ...] = (),
        created_at: datetime,
    ) -> HistoryEntry:
        """写入历史前统一走 ModelGateway 的 compaction 脱敏边界。"""
        safe_text = self._gateway.sanitize_compaction(task_id, text).cloud_text
        return self._store.add(
            task_id=task_id,
            run_id=run_id,
            text=safe_text,
            references=references,
            created_at=created_at,
        )

    def search(self, query: str, *, limit: int = 20) -> tuple[HistoryHit, ...]:
        """只检索独立历史表；项目跨文件查询仍由 ProjectCorpus 负责。"""
        return self._store.search(query, limit=limit)
