"""Phase 07：SQLite durable orchestration、恢复、取消和 lease fencing。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dataharness.domain import (
    ContentHash,
    Project,
    ProjectId,
    ProjectSnapshot,
    RunStatus,
    SnapshotId,
    StepId,
    TaskStatus,
    WaitReason,
)
from dataharness.idgen import DeterministicIdFactory
from dataharness.orchestration import (
    ExecutionDecision,
    HostCrashError,
    RecoveryDecision,
    RunExecutionContext,
    RunOutcome,
    RunService,
    TaskService,
)
from dataharness.providers.durable import LocalDurableExecutor
from dataharness.providers.workspace import FakeWorkspace
from dataharness.sandbox import SandboxLostError, SandboxMount, SandboxResources, SandboxSpec
from dataharness.storage import (
    CheckpointMetadata,
    LeaseLostError,
    RuntimeConnectionFactory,
    SqliteRuntimeStore,
)
from dataharness.testing import FakeClock

T0 = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "d" * 64


class System:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = FakeClock(T0)
        self.store = SqliteRuntimeStore(RuntimeConnectionFactory(tmp_path / "runtime.db"))
        self.project = Project(id=ProjectId("project"), name="orchestration", created_at=T0)
        self.snapshot = ProjectSnapshot(
            id=SnapshotId("snapshot-v1"), project_id=self.project.id, created_at=T0
        )
        with self.store.unit_of_work() as uow:
            uow.repo.add_project(self.project)
            uow.repo.add_snapshot(self.snapshot)
        self.workspace = FakeWorkspace(tmp_path / "workspace")
        self.tasks = TaskService(
            self.store,
            id_factory=DeterministicIdFactory("task"),
            clock=self.clock.now,
            workspace=self.workspace,
        )
        self.runs = RunService(
            self.store,
            id_factory=DeterministicIdFactory("run"),
            clock=self.clock.now,
            workspace=self.workspace,
        )

    def create_run(self):
        task = self.tasks.create(self.project.id)
        return task, self.runs.create(task.id, self.snapshot.id)


@pytest.mark.asyncio
async def test_success_checkpoint_and_second_worker_do_not_repeat_run(tmp_path: Path) -> None:
    system = System(tmp_path)
    task, run = system.create_run()
    calls: list[RecoveryDecision] = []

    async def handler(context: RunExecutionContext) -> RunOutcome:
        calls.append(context.recovery_decision)
        checkpoint = CheckpointMetadata(
            id="checkpoint-1",
            run_id=context.run.id,
            sequence=1,
            checkpoint_ref="checkpoint:1",
            content_hash=ContentHash("sha256:" + "1" * 64),
            project_snapshot_id=context.run.project_snapshot_id,
            phase=context.run.phase,
            created_at=context.now,
        )
        return RunOutcome(decision=ExecutionDecision.SUCCEEDED, checkpoint=checkpoint)

    executor = LocalDurableExecutor(
        system.store, handler, owner="worker-a", clock=system.clock.now, backoff_base=0
    )
    result = await executor.run_once()
    assert result is not None and result.status is RunStatus.SUCCEEDED
    assert system.runs.get(run.id).project_snapshot_id == system.snapshot.id
    assert system.tasks.get(task.id).status is TaskStatus.COMPLETED
    assert calls == [RecoveryDecision.START_FROM_BEGINNING]
    assert await executor.run_once() is None
    assert calls == [RecoveryDecision.START_FROM_BEGINNING]


@pytest.mark.asyncio
async def test_waiting_resume_and_checkpointed_sandbox_loss_rebuild(tmp_path: Path) -> None:
    system = System(tmp_path)
    task, run = system.create_run()
    from dataharness.providers.sandbox import FakeSandboxProvider

    provider = FakeSandboxProvider()

    def spec_factory(current_run):
        return SandboxSpec(
            project_id=current_run.project_id,
            task_id=current_run.task_id,
            run_id=current_run.id,
            project_snapshot_id=current_run.project_snapshot_id,
            image_digest=DIGEST,
            mounts=(
                SandboxMount(
                    source_ref=f"snapshot:{current_run.project_snapshot_id}",
                    target="/project",
                    read_only=True,
                ),
                SandboxMount(
                    source_ref=f"task:{current_run.task_id}:working",
                    target="/task/working",
                    read_only=False,
                ),
                SandboxMount(
                    source_ref=f"task:{current_run.task_id}:staging",
                    target="/task/staging",
                    read_only=False,
                ),
            ),
            resources=SandboxResources(
                memory_mb=128, disk_mb=256, max_output_bytes=1024, step_timeout_seconds=10
            ),
        )

    decisions: list[RecoveryDecision] = []

    async def handler(context: RunExecutionContext) -> RunOutcome:
        decisions.append(context.recovery_decision)
        if len(decisions) == 1:
            assert context.sandbox_lease is not None
            checkpoint = CheckpointMetadata(
                id="checkpoint-wait",
                run_id=context.run.id,
                sequence=1,
                checkpoint_ref="checkpoint:wait",
                content_hash=ContentHash("sha256:" + "2" * 64),
                project_snapshot_id=context.run.project_snapshot_id,
                sandbox_id=context.sandbox_lease.sandbox_id,
                sandbox_image_digest=context.sandbox_lease.image_digest,
                run_lease_epoch=context.lease_epoch,
                phase=context.run.phase,
                created_at=context.now,
            )
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.USER_INPUT,
                checkpoint=checkpoint,
            )
        assert context.recovered
        return RunOutcome(decision=ExecutionDecision.SUCCEEDED)

    executor = LocalDurableExecutor(
        system.store,
        handler,
        owner="worker-a",
        clock=system.clock.now,
        sandbox=provider,
        sandbox_spec_factory=spec_factory,
        backoff_base=0,
        workspace=system.workspace,
    )
    first = await executor.run_once()
    assert first is not None and first.status is RunStatus.WAITING
    assert system.runs.get(run.id).project_snapshot_id == system.snapshot.id
    system.runs.resume(run.id)
    restarted_executor = LocalDurableExecutor(
        system.store,
        handler,
        owner="worker-b",
        clock=system.clock.now,
        sandbox=provider,
        sandbox_spec_factory=spec_factory,
        backoff_base=0,
        workspace=system.workspace,
    )
    second = await restarted_executor.run_once()
    assert second is not None and second.status is RunStatus.SUCCEEDED
    assert decisions == [
        RecoveryDecision.START_FROM_BEGINNING,
        RecoveryDecision.REBUILD_SANDBOX,
    ]


@pytest.mark.asyncio
async def test_retry_is_classified_bounded_and_reclaimed_by_same_run(tmp_path: Path) -> None:
    system = System(tmp_path)
    task, run = system.create_run()
    calls: list[tuple[bool, RecoveryDecision]] = []

    async def handler(context: RunExecutionContext) -> RunOutcome:
        calls.append((context.recovered, context.recovery_decision))
        raise SandboxLostError("synthetic sandbox loss")

    executor = LocalDurableExecutor(
        system.store,
        handler,
        owner="worker-a",
        clock=system.clock.now,
        max_retries=2,
        backoff_base=0,
    )
    first = await executor.run_once()
    second = await executor.run_once()
    assert first is not None and first.status is RunStatus.RUNNING
    assert second is not None and second.status is RunStatus.RUNNING
    final = await executor.run_once()
    assert final is not None and final.status is RunStatus.FAILED
    assert system.tasks.get(task.id).status is TaskStatus.FAILED
    assert calls == [
        (False, RecoveryDecision.START_FROM_BEGINNING),
        (True, RecoveryDecision.START_FROM_BEGINNING),
        (True, RecoveryDecision.START_FROM_BEGINNING),
    ]
    with system.store.unit_of_work() as uow:
        assert uow.repo.count_retry_attempts(run.id) == 2


@pytest.mark.asyncio
async def test_host_crash_recovers_same_run_and_does_not_reopen_terminal_run(
    tmp_path: Path,
) -> None:
    system = System(tmp_path)
    task, run = system.create_run()
    calls = 0

    async def first_handler(context: RunExecutionContext) -> RunOutcome:
        nonlocal calls
        calls += 1
        raise HostCrashError("synthetic host crash")

    crashing_executor = LocalDurableExecutor(
        system.store,
        first_handler,
        owner="worker-a",
        clock=system.clock.now,
        max_retries=1,
        backoff_base=0,
    )
    recovered = await crashing_executor.run_once()
    assert recovered is not None and recovered.status is RunStatus.RUNNING

    async def recovered_handler(context: RunExecutionContext) -> RunOutcome:
        assert context.recovered
        assert context.run.id == run.id
        assert context.run.project_snapshot_id == system.snapshot.id
        return RunOutcome(decision=ExecutionDecision.SUCCEEDED)

    restarted = LocalDurableExecutor(
        system.store,
        recovered_handler,
        owner="worker-b",
        clock=system.clock.now,
        max_retries=1,
        backoff_base=0,
    )
    result = await restarted.run_once()
    assert result is not None and result.status is RunStatus.SUCCEEDED
    assert system.tasks.get(task.id).status is TaskStatus.COMPLETED
    assert await restarted.run_once() is None


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_cleans_only_unpublished_staging(tmp_path: Path) -> None:
    system = System(tmp_path)
    task, run = system.create_run()
    staged = system.workspace.staging_path(
        system.project.id, task.id, StepId("step-unpublished"), "draft.txt"
    )
    staged.write_text("not yet published", encoding="utf-8")
    assert staged.exists()
    first = system.runs.cancel(run.id)
    second = system.runs.cancel(run.id)
    assert first.status is RunStatus.CANCELLED
    assert second == first
    assert system.tasks.get(task.id).status is TaskStatus.CANCELLED
    assert not staged.exists()

    async def should_not_run(context: RunExecutionContext) -> RunOutcome:
        raise AssertionError("cancelled Run must not invoke handler")

    executor = LocalDurableExecutor(
        system.store, should_not_run, owner="worker-a", clock=system.clock.now
    )
    assert await executor.run_once() is None


def test_old_worker_epoch_cannot_commit_after_reclaim(tmp_path: Path) -> None:
    system = System(tmp_path)
    _, run = system.create_run()
    first = system.store.claim_next_run("worker-a", T0, timedelta(seconds=1))
    assert first is not None
    second = system.store.claim_next_run(
        "worker-b", T0 + timedelta(seconds=2), timedelta(seconds=1)
    )
    assert second is not None and second.lease.epoch > first.lease.epoch
    with pytest.raises(LeaseLostError), system.store.unit_of_work(immediate=True) as uow:
        current = uow.repo.get_run(run.id)
        uow.repo.save_run(
            current.value.fail(T0 + timedelta(seconds=2)),
            first.version,
            "STALE_WORKER_COMMIT",
            lease=first.lease,
            lease_checked_at=T0 + timedelta(seconds=2),
        )
