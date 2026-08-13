"""可恢复的正式输出发布编排。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime

from dataharness.domain import ContentHash, ProjectId, RunId, StepId, TaskId, utcnow

from .errors import PublicationError, ResourceIntegrityError
from .models import PublicationKind, PublicationRecord, PublicationStatus, WorkspaceResource
from .protocols import PublicationJournal, VirtualWorkspace


class WorkspaceBridge:
    """把 staging 文件、Runtime 发布记录和正式 Workspace 位置安全地收敛。

    SQLite 和文件系统之间不存在跨介质事务，因此协议先持久化 ``STAGED``，再原子
    移动文件，最后标记 ``AVAILABLE``。任一步崩溃后都可由 :meth:`reconcile` 重放。
    """

    def __init__(
        self,
        workspace: VirtualWorkspace,
        journal: PublicationJournal,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._workspace = workspace
        self._journal = journal
        self._clock = clock

    @staticmethod
    def idempotency_key(run_id: RunId, step_id: StepId, output_name: str) -> str:
        """生成稳定幂等键；分隔符不会进入各 ID/文件名的受控值。"""
        return f"{run_id}:{step_id}:{output_name}"

    def stage(
        self,
        *,
        project_id: ProjectId,
        task_id: TaskId,
        run_id: RunId,
        step_id: StepId,
        output_name: str,
        kind: PublicationKind,
        resource_id: str,
        content_hash: ContentHash,
        byte_size: int,
    ) -> PublicationRecord:
        """登记已由 Sandbox 写完并由 Host 校验过的 staging 输出。"""
        key = self.idempotency_key(run_id, step_id, output_name)
        now = self._clock()
        record = PublicationRecord(
            idempotency_key=key,
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            step_id=step_id,
            output_name=output_name,
            kind=kind,
            resource_id=resource_id,
            content_hash=content_hash,
            byte_size=byte_size,
            status=PublicationStatus.STAGED,
            created_at=now,
            updated_at=now,
        )
        return self._journal.stage(record)

    def publish(self, idempotency_key: str) -> WorkspaceResource:
        """幂等发布；只有哈希和大小均匹配的文件才可进入 AVAILABLE。"""
        record = self._journal.get(idempotency_key)
        if record is None:
            raise PublicationError(f"发布记录不存在：{idempotency_key}")
        if record.status == PublicationStatus.CORRUPT:
            raise PublicationError(f"发布记录已损坏：{idempotency_key}")
        try:
            resource = (
                self._workspace.published_resource(record)
                if record.status == PublicationStatus.AVAILABLE
                else self._workspace.publish_staged(record)
            )
        except (FileNotFoundError, ResourceIntegrityError) as error:
            self._journal.set_status(idempotency_key, PublicationStatus.CORRUPT, str(error))
            raise PublicationError(str(error)) from error
        self._journal.set_status(idempotency_key, PublicationStatus.AVAILABLE)
        return resource

    def reconcile(self) -> tuple[PublicationRecord, ...]:
        """对账全部非 AVAILABLE 记录，返回对账后的最终记录。"""
        reconciled: list[PublicationRecord] = []
        for record in self._journal.pending():
            with suppress(PublicationError):
                self.publish(record.idempotency_key)
            final = self._journal.get(record.idempotency_key)
            assert final is not None
            reconciled.append(final)
        return tuple(reconciled)

    def available(self, project_id: ProjectId) -> tuple[PublicationRecord, ...]:
        """上层唯一可见列表；STAGED/CORRUPT 永远不会被返回。"""
        return self._journal.available(project_id)
