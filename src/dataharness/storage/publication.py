"""Runtime SQLite 发布日志 Adapter。"""

from __future__ import annotations

from sqlite3 import IntegrityError, Row

from dataharness.domain import ContentHash, ProjectId, RunId, StepId, TaskId
from dataharness.workspace import (
    PublicationError,
    PublicationKind,
    PublicationRecord,
    PublicationStatus,
)

from .database import RuntimeConnectionFactory


def _record(row: Row) -> PublicationRecord:
    """把窄元数据行恢复为冻结值对象。"""
    from datetime import datetime

    return PublicationRecord(
        idempotency_key=row["idempotency_key"],
        project_id=ProjectId(row["project_id"]),
        task_id=TaskId(row["task_id"]),
        run_id=RunId(row["run_id"]),
        step_id=StepId(row["step_id"]),
        output_name=row["output_name"],
        kind=PublicationKind(row["kind"]),
        resource_id=row["resource_id"],
        content_hash=ContentHash(row["content_hash"]),
        byte_size=row["byte_size"],
        status=PublicationStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        detail=row["detail"],
    )


class SqlitePublicationJournal:
    """发布状态的 Runtime SQLite 事实源，支持幂等登记与恢复扫描。"""

    def __init__(self, factory: RuntimeConnectionFactory) -> None:
        self._factory = factory

    def stage(self, record: PublicationRecord) -> PublicationRecord:
        connection = self._factory.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM workspace_publications WHERE idempotency_key = ?",
                (record.idempotency_key,),
            ).fetchone()
            if existing is not None:
                value = _record(existing)
                comparable = (
                    "project_id",
                    "task_id",
                    "run_id",
                    "step_id",
                    "output_name",
                    "kind",
                    "resource_id",
                    "content_hash",
                    "byte_size",
                )
                if any(getattr(value, key) != getattr(record, key) for key in comparable):
                    raise PublicationError("相同幂等键对应不同发布请求")
                connection.commit()
                return value
            connection.execute(
                "INSERT INTO workspace_publications(idempotency_key, project_id, task_id, run_id, "
                "step_id, output_name, kind, resource_id, content_hash, byte_size, status, detail, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.idempotency_key,
                    str(record.project_id),
                    str(record.task_id),
                    str(record.run_id),
                    str(record.step_id),
                    record.output_name,
                    record.kind,
                    record.resource_id,
                    record.content_hash,
                    record.byte_size,
                    record.status,
                    record.detail,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            connection.commit()
            return record
        except IntegrityError as error:
            connection.rollback()
            raise PublicationError("发布记录违反归属或唯一性约束") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, idempotency_key: str) -> PublicationRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM workspace_publications WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return _record(row) if row is not None else None
        finally:
            connection.close()

    def set_status(
        self, idempotency_key: str, status: PublicationStatus, detail: str | None = None
    ) -> PublicationRecord:
        from dataharness.domain import utcnow

        connection = self._factory.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE workspace_publications SET status = ?, detail = ?, updated_at = ? "
                "WHERE idempotency_key = ?",
                (status, detail, utcnow().isoformat(), idempotency_key),
            )
            if result.rowcount != 1:
                raise PublicationError(f"发布记录不存在：{idempotency_key}")
            row = connection.execute(
                "SELECT * FROM workspace_publications WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            assert row is not None
            connection.commit()
            return _record(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending(self) -> tuple[PublicationRecord, ...]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM workspace_publications WHERE status <> 'AVAILABLE' "
                "ORDER BY created_at, idempotency_key"
            ).fetchall()
            return tuple(_record(row) for row in rows)
        finally:
            connection.close()

    def available(self, project_id: ProjectId) -> tuple[PublicationRecord, ...]:
        """只暴露已完成双写收敛的正式输出。"""
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM workspace_publications WHERE project_id = ? "
                "AND status = 'AVAILABLE' "
                "ORDER BY created_at, idempotency_key",
                (str(project_id),),
            ).fetchall()
            return tuple(_record(row) for row in rows)
        finally:
            connection.close()
