# ruff: noqa: E501
"""面向 orchestration 的 Runtime storage facade 与耐久队列原语。"""

from __future__ import annotations

from datetime import datetime, timedelta

from dataharness.domain import Run, RunId, RunStatus

from .database import RuntimeConnectionFactory
from .errors import LeaseLostError
from .records import ClaimedRun, RetryRecord, RunLease
from .repository import RuntimeRepository
from .uow import UnitOfWork


class SqliteRuntimeStore:
    """生产 Runtime SQLite Adapter。

    普通元数据修改经 ``unit_of_work``；claim/heartbeat 是封装完整事务的原子原语，
    调用方无法在领取候选与写入 fencing token 之间插入非原子逻辑。
    """

    def __init__(self, factory: RuntimeConnectionFactory) -> None:
        self.factory = factory
        # 构造时完成 schema 检查，使后续并发 Worker 不在首个 claim 时争抢迁移锁。
        connection = factory.connect()
        connection.close()

    def unit_of_work(self, *, immediate: bool = False) -> UnitOfWork:
        """创建一次性事务边界。"""
        return UnitOfWork(self.factory, immediate=immediate)

    def claim_next_run(
        self, owner: str, now: datetime, lease_duration: timedelta
    ) -> ClaimedRun | None:
        """原子领取最早 QUEUED Run，或回收最早已过期的 RUNNING lease。

        ``BEGIN IMMEDIATE`` 会在读取候选前取得 SQLite writer reservation；两个 Worker
        即使同时开始，也只能依次观察并更新，因此不会同时持有同一有效 epoch。
        """
        if not owner.strip():
            raise ValueError("lease owner 不能为空")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration 必须为正")
        expires_at = now + lease_duration
        with self.unit_of_work(immediate=True) as uow:
            connection = uow.repo._connection  # 原子原语与 Repository 共享同一受控事务。
            row = connection.execute(
                "SELECT * FROM runs WHERE "
                "(status = 'QUEUED' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)) "
                "OR (status = 'RUNNING' AND "
                "    ((lease_owner IS NOT NULL AND lease_expires_at <= ?) "
                "     OR (lease_owner IS NULL AND next_attempt_at IS NOT NULL AND next_attempt_at <= ?))) "
                "ORDER BY CASE WHEN status = 'QUEUED' THEN 0 ELSE 1 END, created_at, id LIMIT 1",
                (now.isoformat(), now.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            recovered = row["status"] == RunStatus.RUNNING
            new_status = RunStatus.RUNNING
            result = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, next_attempt_at = NULL, lease_owner = ?, lease_epoch = lease_epoch + 1, "
                "lease_expires_at = ?, heartbeat_at = ?, row_version = row_version + 1 "
                "WHERE id = ? AND row_version = ?",
                (
                    new_status,
                    now.isoformat(),
                    owner,
                    expires_at.isoformat(),
                    now.isoformat(),
                    row["id"],
                    row["row_version"],
                ),
            )
            if result.rowcount != 1:
                raise LeaseLostError(f"Run {row['id']} 在领取期间发生并发变化")
            updated = connection.execute("SELECT * FROM runs WHERE id = ?", (row["id"],)).fetchone()
            assert updated is not None
            event_type = "RUN_LEASE_RECOVERED" if recovered else "RUN_CLAIMED"
            uow.repo.append_event(
                "run",
                row["id"],
                event_type,
                now,
                {"owner": owner, "lease_epoch": updated["lease_epoch"]},
            )
            lease = RunLease(
                run_id=RunId(row["id"]),
                owner=owner,
                epoch=updated["lease_epoch"],
                expires_at=expires_at,
                heartbeat_at=now,
            )
            return ClaimedRun(
                run=RuntimeRepository._run_from_row(updated),
                version=updated["row_version"],
                lease=lease,
                recovered=recovered,
            )

    def heartbeat(self, lease: RunLease, now: datetime, lease_duration: timedelta) -> RunLease:
        """仅当前且未过期 epoch 可续租；过期 lease 必须重新 claim 取得新 epoch。"""
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration 必须为正")
        expires_at = now + lease_duration
        with self.unit_of_work(immediate=True) as uow:
            result = uow.repo._connection.execute(
                "UPDATE runs SET heartbeat_at = ?, lease_expires_at = ? "
                "WHERE id = ? AND lease_owner = ? AND lease_epoch = ? AND status = 'RUNNING' "
                "AND lease_expires_at > ?",
                (
                    now.isoformat(),
                    expires_at.isoformat(),
                    str(lease.run_id),
                    lease.owner,
                    lease.epoch,
                    now.isoformat(),
                ),
            )
            if result.rowcount != 1:
                raise LeaseLostError(f"Run {lease.run_id} 的 lease 已过期或被新 epoch 取代")
            uow.repo.append_event(
                "run", str(lease.run_id), "RUN_HEARTBEAT", now, {"lease_epoch": lease.epoch}
            )
        return lease.model_copy(update={"heartbeat_at": now, "expires_at": expires_at})

    def request_cancel(self, run_id: RunId, now: datetime) -> Run:
        """持久化幂等取消。

        QUEUED 可立即收口；RUNNING 只记录取消意图，交给持有有效 worker lease 的
        执行路径终止 Sandbox；WAITING 先恢复为可取消的 RUNNING，再立即收口，避免
        产生永远不会被 worker 领取的 WAITING + cancel_requested 状态。
        """
        with self.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(run_id)
            current = stored.value
            if current.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
                return current
            if current.status == RunStatus.QUEUED:
                updated = current.cancel(now)
                return uow.repo.save_run(updated, stored.version, "RUN_CANCELLED").value
            if current.status == RunStatus.WAITING:
                resumed = current.resume(now)
                resumed_record = uow.repo.save_run(
                    resumed, stored.version, "RUN_RESUMED_FOR_CANCEL"
                )
                updated = resumed.cancel(now)
                return uow.repo.save_run(updated, resumed_record.version, "RUN_CANCELLED").value
            updated = current.request_cancel(now)
            return uow.repo.save_run(updated, stored.version, "RUN_CANCEL_REQUESTED").value

    def resume_waiting(self, run_id: RunId, now: datetime) -> Run:
        """将 WAITING Run 恢复为可被 worker 领取的 RUNNING 状态。"""
        with self.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(run_id)
            updated = stored.value.resume(now)
            saved = uow.repo.save_run(updated, stored.version, "RUN_RESUMED")
            uow.repo._connection.execute(
                "UPDATE runs SET next_attempt_at = ? WHERE id = ? AND row_version = ?",
                (now.isoformat(), str(run_id), saved.version),
            )
            return saved.value

    def schedule_retry(
        self,
        lease: RunLease,
        *,
        attempt: int,
        failure_kind: str,
        delay: timedelta,
        now: datetime,
    ) -> RetryRecord:
        """在同一事务中记录失败分类、释放旧 lease 并安排下一次领取。"""
        if attempt <= 0:
            raise ValueError("attempt 必须为正")
        if delay < timedelta(0):
            raise ValueError("delay 不能为负")
        next_attempt_at = now + delay
        retry = RetryRecord(
            run_id=lease.run_id,
            attempt=attempt,
            failure_kind=failure_kind,
            delay_seconds=delay.total_seconds(),
            next_attempt_at=next_attempt_at,
            created_at=now,
        )
        with self.unit_of_work(immediate=True) as uow:
            connection = uow.repo._connection
            result = connection.execute(
                "UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, "
                "next_attempt_at = ?, updated_at = ?, row_version = row_version + 1 "
                "WHERE id = ? AND status = 'RUNNING' AND lease_owner = ? AND lease_epoch = ? "
                "AND lease_expires_at > ?",
                (
                    next_attempt_at.isoformat(),
                    now.isoformat(),
                    str(lease.run_id),
                    lease.owner,
                    lease.epoch,
                    now.isoformat(),
                ),
            )
            if result.rowcount != 1:
                raise LeaseLostError(f"Run {lease.run_id} 的 lease 已失效，不能安排重试")
            return uow.repo.add_retry_attempt(retry)
