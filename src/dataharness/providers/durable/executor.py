"""基于 Runtime SQLite 的本地耐久执行器与 worker loop。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta

from dataharness.domain import Run, RunStatus, TaskStatus, WaitReason, utcnow
from dataharness.orchestration.errors import (
    PolicyDeniedError,
    RetryLimitExceeded,
    classify_error,
)
from dataharness.orchestration.models import (
    ExecutionDecision,
    FailureClass,
    RecoveryDecision,
    RunOutcome,
)
from dataharness.orchestration.protocols import (
    RunExecutionContext,
    RunHandler,
    SandboxSpecFactory,
)
from dataharness.sandbox import (
    SandboxLease,
    SandboxLostError,
    SandboxPolicyError,
    SandboxProvider,
)
from dataharness.storage import CheckpointMetadata, ClaimedRun, LeaseLostError, SqliteRuntimeStore
from dataharness.workspace import VirtualWorkspace


class LocalDurableExecutor:
    """单机 SQLite durable executor。

    每次 claim 都产生新的 ``lease_epoch``。任何状态提交、重试安排和心跳都必须带上
    owner/epoch/未过期条件；旧 worker 即使在内存中继续运行，也只能得到 LeaseLostError，
    不能覆盖新 worker 的事实。已完成 Step 的“不重复执行”由 handler 使用 checkpoint/
    AnalysisRuntime 幂等键保证，本类只负责把 checkpoint 作为恢复入口传入。
    """

    def __init__(
        self,
        store: SqliteRuntimeStore,
        handler: RunHandler,
        *,
        owner: str,
        lease_duration: timedelta = timedelta(seconds=30),
        max_retries: int = 3,
        backoff_base: float = 0.25,
        clock: Callable[[], datetime] = utcnow,
        sandbox: SandboxProvider | None = None,
        sandbox_spec_factory: SandboxSpecFactory | None = None,
        workspace: VirtualWorkspace | None = None,
        cancel_grace_seconds: float = 0.25,
    ) -> None:
        if not owner.strip():
            raise ValueError("worker owner 不能为空")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration 必须为正")
        if max_retries < 0:
            raise ValueError("max_retries 不能为负")
        if backoff_base < 0:
            raise ValueError("backoff_base 不能为负")
        self._store = store
        self._handler = handler
        self._owner = owner
        self._lease_duration = lease_duration
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._clock = clock
        self._sandbox = sandbox
        self._sandbox_spec_factory = sandbox_spec_factory
        self._workspace = workspace
        self._cancel_grace_seconds = cancel_grace_seconds

    async def run_once(self) -> Run | None:
        """领取并执行一个 Run；队列为空返回 None。"""
        claimed = self._store.claim_next_run(self._owner, self._clock(), self._lease_duration)
        if claimed is None:
            return None
        return await self._execute_claim(claimed)

    async def run_worker(
        self, stop_event: asyncio.Event, *, idle_sleep_seconds: float = 0.1
    ) -> None:
        """可停止的 worker loop；等待期间不持有任何 Run lease。"""
        if idle_sleep_seconds < 0:
            raise ValueError("idle_sleep_seconds 不能为负")
        while not stop_event.is_set():
            result = await self.run_once()
            if result is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), idle_sleep_seconds)

    async def _execute_claim(self, claimed: ClaimedRun) -> Run:
        run = claimed.run
        checkpoint = self._latest_checkpoint(run)
        decision = self._recovery_decision(run, claimed.recovered, checkpoint)
        if run.cancel_requested_at is not None:
            return await self._cancel_claim(claimed, checkpoint)

        sandbox_lease: SandboxLease | None = None
        try:
            sandbox_lease, decision = await self._restore_sandbox(run, checkpoint, decision)
            context = RunExecutionContext(
                run,
                worker_owner=self._owner,
                lease_epoch=claimed.lease.epoch,
                recovered=claimed.recovered,
                recovery_decision=decision,
                checkpoint_ref=checkpoint.checkpoint_ref if checkpoint else None,
                sandbox_lease=sandbox_lease,
                now=self._clock(),
            )
            outcome = await self._handler(context)
        except asyncio.CancelledError:
            # Host task 被取消不等于可以丢掉 Run；释放 Sandbox 后让 lease 到期恢复。
            await self._terminate_sandbox(sandbox_lease)
            raise
        except Exception as error:
            return await self._handle_failure(claimed, error, sandbox_lease)

        try:
            return await self._commit_outcome(claimed, outcome, checkpoint, sandbox_lease)
        except LeaseLostError:
            await self._terminate_sandbox(sandbox_lease)
            raise

    def _latest_checkpoint(self, run: Run) -> CheckpointMetadata | None:
        with self._store.unit_of_work() as uow:
            return uow.repo.latest_checkpoint(run.id)

    @staticmethod
    def _recovery_decision(
        run: Run, recovered: bool, checkpoint: CheckpointMetadata | None
    ) -> RecoveryDecision:
        if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            return RecoveryDecision.TERMINAL
        if not recovered:
            return RecoveryDecision.START_FROM_BEGINNING
        if checkpoint is None:
            return RecoveryDecision.START_FROM_BEGINNING
        if checkpoint.project_snapshot_id != run.project_snapshot_id:
            raise PolicyDeniedError("checkpoint 的 ProjectSnapshot 与 Run 不一致")
        return (
            RecoveryDecision.RESUME_FROM_CHECKPOINT
            if checkpoint.sandbox_id
            else RecoveryDecision.REBUILD_SANDBOX
        )

    async def _restore_sandbox(
        self,
        run: Run,
        checkpoint: CheckpointMetadata | None,
        decision: RecoveryDecision,
    ) -> tuple[SandboxLease | None, RecoveryDecision]:
        if self._sandbox is None or self._sandbox_spec_factory is None:
            return None, decision
        spec = self._sandbox_spec_factory(run)
        if (
            checkpoint is not None
            and checkpoint.sandbox_image_digest is not None
            and checkpoint.sandbox_image_digest != spec.image_digest
        ):
            raise PolicyDeniedError("checkpoint 的 Sandbox 镜像 digest 与恢复规格不一致")
        if decision == RecoveryDecision.RESUME_FROM_CHECKPOINT and checkpoint is not None:
            try:
                lease = await self._sandbox.connect(checkpoint.sandbox_id or "")
                if (
                    lease.run_id != run.id
                    or lease.task_id != run.task_id
                    or lease.project_id != run.project_id
                    or lease.project_snapshot_id != run.project_snapshot_id
                    or (
                        checkpoint.sandbox_image_digest is not None
                        and lease.image_digest != checkpoint.sandbox_image_digest
                    )
                ):
                    raise PolicyDeniedError("恢复的 Sandbox lease 上下文与 Run 不一致")
                return lease, decision
            except (SandboxLostError, SandboxPolicyError):
                # Provider 进程重启或旧 Sandbox 被销毁后，Provider 可能无法区分
                # “远端丢失”与“本地没有已认证 lease”。两者都不能继续使用旧句柄；
                # 先丢弃旧 lease，再按已经校验过的 Run/Snapshot/digest 规格重建。
                decision = RecoveryDecision.REBUILD_SANDBOX
        if decision in (
            RecoveryDecision.START_FROM_BEGINNING,
            RecoveryDecision.REBUILD_SANDBOX,
        ):
            return await self._sandbox.create(spec), decision
        return None, decision

    async def _commit_outcome(
        self,
        claimed: ClaimedRun,
        outcome: RunOutcome,
        previous_checkpoint: CheckpointMetadata | None,
        sandbox_lease: SandboxLease | None,
    ) -> Run:
        now = self._clock()
        if outcome.checkpoint is not None:
            self._validate_checkpoint(claimed.run, outcome.checkpoint, sandbox_lease)
            with self._store.unit_of_work() as uow:
                uow.repo.add_checkpoint(outcome.checkpoint)

        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(claimed.run.id)
            if stored.value.cancel_requested_at is not None:
                updated = stored.value.cancel(now)
                result = uow.repo.save_run(
                    updated,
                    stored.version,
                    "RUN_CANCELLED",
                    lease=claimed.lease,
                    lease_checked_at=now,
                ).value
                cancelled = True
            elif outcome.decision == ExecutionDecision.WAITING:
                updated = stored.value.wait(outcome.wait_reason or WaitReason.USER_INPUT, now)
                saved = uow.repo.save_run(
                    updated,
                    stored.version,
                    "RUN_WAITING",
                    lease=claimed.lease,
                    lease_checked_at=now,
                )
                result = saved.value
                cancelled = False
            else:
                cancelled = False
                updated = stored.value
                if outcome.phase is not None and outcome.phase != updated.phase:
                    updated = updated.advance_phase(outcome.phase, now)
                    saved_phase = uow.repo.save_run(
                        updated,
                        stored.version,
                        "RUN_PHASE_ADVANCED",
                        lease=claimed.lease,
                        lease_checked_at=now,
                    )
                    stored_version = saved_phase.version
                    updated = updated.succeed(now)
                else:
                    stored_version = stored.version
                    updated = updated.succeed(now)
                result = uow.repo.save_run(
                    updated,
                    stored_version,
                    "RUN_SUCCEEDED",
                    lease=claimed.lease,
                    lease_checked_at=now,
                ).value
        await self._terminate_sandbox(sandbox_lease)
        if cancelled:
            self._cancel_task(result)
            if self._workspace is not None:
                self._workspace.cleanup_staging(result.project_id, result.task_id)
        elif result.status == RunStatus.SUCCEEDED:
            self._complete_task(result)
        return result

    def _validate_checkpoint(
        self, run: Run, checkpoint: CheckpointMetadata, sandbox_lease: SandboxLease | None
    ) -> None:
        if checkpoint.run_id != run.id or checkpoint.project_snapshot_id != run.project_snapshot_id:
            raise PolicyDeniedError("checkpoint 必须绑定当前 Run 的固定 Snapshot")
        if sandbox_lease is not None:
            if checkpoint.sandbox_id != sandbox_lease.sandbox_id:
                raise PolicyDeniedError("checkpoint 的 Sandbox lease 与当前执行不一致")
            if checkpoint.sandbox_image_digest != sandbox_lease.image_digest:
                raise PolicyDeniedError("checkpoint 的 Sandbox 镜像 digest 不一致")

    async def _handle_failure(
        self, claimed: ClaimedRun, error: BaseException, sandbox_lease: SandboxLease | None
    ) -> Run:
        failure_class, retryable = classify_error(error)
        await self._terminate_sandbox(sandbox_lease)
        if failure_class == FailureClass.BUDGET_EXHAUSTED:
            return self._wait_for_budget(claimed)
        with self._store.unit_of_work() as uow:
            attempts = uow.repo.count_retry_attempts(claimed.run.id)
        if retryable and attempts < self._max_retries:
            delay = timedelta(seconds=self._backoff_base * (2**attempts))
            self._store.schedule_retry(
                claimed.lease,
                attempt=attempts + 1,
                failure_kind=failure_class,
                delay=delay,
                now=self._clock(),
            )
            return self._get_run(claimed.run.id)
        if retryable and attempts >= self._max_retries:
            error = RetryLimitExceeded(f"Run 自动重试达到上限：{self._max_retries}")
        return self._fail_claim(claimed, error, failure_class)

    def _wait_for_budget(self, claimed: ClaimedRun) -> Run:
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(claimed.run.id)
            updated = stored.value.wait(WaitReason.BUDGET_EXHAUSTED, now)
            return uow.repo.save_run(
                updated,
                stored.version,
                "RUN_WAITING_BUDGET",
                lease=claimed.lease,
                lease_checked_at=now,
            ).value

    def _fail_claim(
        self, claimed: ClaimedRun, error: BaseException, failure_class: FailureClass
    ) -> Run:
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(claimed.run.id)
            updated = stored.value.fail(now)
            result = uow.repo.save_run(
                updated,
                stored.version,
                "RUN_FAILED",
                lease=claimed.lease,
                lease_checked_at=now,
            ).value
        self._fail_task(result, failure_class)
        return result

    async def _cancel_claim(
        self, claimed: ClaimedRun, checkpoint: CheckpointMetadata | None
    ) -> Run:
        # 先终止外部副作用，再提交终态；旧 worker 无法用旧 epoch 越过此提交。
        if self._sandbox is not None and checkpoint is not None and checkpoint.sandbox_id:
            with suppress(SandboxLostError):
                lease = await self._sandbox.connect(checkpoint.sandbox_id)
                await self._sandbox.terminate(lease)
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(claimed.run.id)
            updated = stored.value.cancel(now)
            result = uow.repo.save_run(
                updated,
                stored.version,
                "RUN_CANCELLED",
                lease=claimed.lease,
                lease_checked_at=now,
            ).value
        self._cancel_task(result)
        if self._workspace is not None:
            self._workspace.cleanup_staging(result.project_id, result.task_id)
        return result

    async def _terminate_sandbox(self, lease: SandboxLease | None) -> None:
        if lease is None or self._sandbox is None:
            return
        with suppress(Exception):
            await self._sandbox.terminate(lease)

    def _get_run(self, run_id):
        with self._store.unit_of_work() as uow:
            return uow.repo.get_run(run_id).value

    def _complete_task(self, run: Run) -> None:
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_task(run.task_id)
            if stored.value.status == TaskStatus.ACTIVE:
                uow.repo.save_task(stored.value.complete(now), stored.version, "TASK_COMPLETED")

    def _fail_task(self, run: Run, failure_class: FailureClass) -> None:
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_task(run.task_id)
            if stored.value.status == TaskStatus.ACTIVE:
                uow.repo.save_task(stored.value.fail(now), stored.version, "TASK_FAILED")

    def _cancel_task(self, run: Run) -> None:
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_task(run.task_id)
            if stored.value.status in (TaskStatus.QUEUED, TaskStatus.ACTIVE):
                uow.repo.save_task(stored.value.cancel(now), stored.version, "TASK_CANCELLED")
